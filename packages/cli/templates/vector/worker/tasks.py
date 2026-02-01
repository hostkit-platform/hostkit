"""Celery tasks for document processing."""

import hashlib
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Ensure parent directory is in path for imports
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

# Now we can import our modules
# Use direct file imports to avoid __init__.py chain issues
import importlib.util

def _import_module(name: str, filepath: str):
    """Import a module directly from its file path."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

# Import config
_config = _import_module("_config", os.path.join(_parent_dir, "config.py"))
settings = _config.settings

logger = logging.getLogger(__name__)


def get_sync_service_session() -> Session:
    """Get synchronous service database session for Celery tasks."""
    db_url = settings.SERVICE_DATABASE_URL.replace("+asyncpg", "")
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def get_sync_project_session(project_name: str) -> Session:
    """Get synchronous project database session for Celery tasks."""
    # Extract password from service URL
    password = settings.SERVICE_DATABASE_URL.split(":")[-1].split("@")[0]
    db_url = settings.PROJECT_DATABASE_TEMPLATE.format(
        project=project_name,
        password=password,
    ).replace("+asyncpg", "")
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _get_models():
    """Lazy load models to avoid import chain issues."""
    from models.service import VectorJob
    from models.project import Collection, Document, Chunk
    return VectorJob, Collection, Document, Chunk


def _get_services():
    """Lazy load services to avoid import chain issues."""
    from services.chunking import get_chunking_service
    from services.embedding import EmbeddingService
    return get_chunking_service, EmbeddingService


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_ingestion(
    self,
    job_id: str,
    project_name: str,
    collection_name: str,
    source_type: str,
    content: Optional[str] = None,
    source_url: Optional[str] = None,
    file_path: Optional[str] = None,
    source_name: Optional[str] = None,
    extra_data: Optional[dict] = None,
):
    """
    Process document ingestion asynchronously.

    This task:
    1. Updates job status to processing
    2. Fetches/reads content from source
    3. Parses content (if needed)
    4. Chunks the text
    5. Generates embeddings
    6. Stores chunks in database
    7. Updates job status to completed
    """
    # Lazy load models and services
    VectorJob, Collection, Document, Chunk = _get_models()
    get_chunking_service, EmbeddingService = _get_services()

    service_session = get_sync_service_session()
    project_session = get_sync_project_session(project_name)

    try:
        # Update job status to processing
        job = service_session.query(VectorJob).filter(VectorJob.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        job.status = "processing"
        job.started_at = job.started_at or datetime.utcnow()
        service_session.commit()

        # Get collection
        collection = project_session.query(Collection).filter(
            Collection.name == collection_name
        ).first()
        if not collection:
            raise ValueError(f"Collection '{collection_name}' not found")

        # Get content based on source type
        parsed_metadata = {}
        if source_type == "text":
            text_content = content
            if not text_content:
                raise ValueError("No content provided for text source")
        elif source_type == "url":
            text_content, parsed_metadata = fetch_url_content(source_url)
            source_name = source_name or parsed_metadata.get("title") or source_url
        elif source_type == "file":
            text_content, parsed_metadata = read_file_content(file_path)
            source_name = source_name or parsed_metadata.get("title") or file_path.split("/")[-1]
            # Clean up uploaded file after reading
            from services.file_storage import delete_upload
            delete_upload(file_path)
        else:
            raise ValueError(f"Unknown source type: {source_type}")

        # Merge parsed metadata with provided metadata
        final_metadata = {**parsed_metadata, **(extra_data or {})}

        # Update progress
        job.progress = 20
        service_session.commit()

        # Create content hash
        content_hash = hashlib.sha256(text_content.encode()).hexdigest()

        # Check for duplicate
        existing = project_session.query(Document).filter(
            Document.collection_id == collection.id,
            Document.content_hash == content_hash,
        ).first()
        if existing:
            raise ValueError("Document with identical content already exists")

        # Create document record
        document = Document(
            collection_id=collection.id,
            source_type=source_type,
            source_name=source_name,
            source_url=source_url if source_type == "url" else None,
            content_hash=content_hash,
            extra_data=final_metadata,
        )
        project_session.add(document)
        project_session.flush()

        # Update progress
        job.progress = 40
        service_session.commit()

        # Chunk content
        chunking_service = get_chunking_service()
        chunk_data_list = chunking_service.chunk_text(
            text_content,
            source_extra_data={"document_id": document.id},
        )

        if not chunk_data_list:
            raise ValueError("No content to chunk")

        job.progress = 60
        service_session.commit()

        # Generate embeddings (run async in sync context)
        import asyncio
        embedding_service = EmbeddingService()
        texts = [cd.content for cd in chunk_data_list]

        # Run async embedding in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            embedding_result = loop.run_until_complete(
                embedding_service.embed_texts(texts)
            )
        finally:
            loop.close()

        job.progress = 80
        service_session.commit()

        # Create chunk records
        chunks = []
        for i, (chunk_data, embedding) in enumerate(
            zip(chunk_data_list, embedding_result.embeddings)
        ):
            chunk = Chunk(
                collection_id=collection.id,
                document_id=document.id,
                content=chunk_data.content,
                embedding=embedding,
                chunk_index=i,
                token_count=chunk_data.token_count,
                extra_data=chunk_data.extra_data,
            )
            chunks.append(chunk)

        project_session.add_all(chunks)

        # Update document stats
        document.chunk_count = len(chunks)
        document.token_count = sum(c.token_count for c in chunks)

        # Update collection stats
        collection.document_count += 1
        collection.chunk_count += len(chunks)

        project_session.commit()

        # Update job as completed
        job.status = "completed"
        job.progress = 100
        job.completed_at = datetime.utcnow()
        job.chunks_created = len(chunks)
        job.tokens_used = embedding_result.tokens_used
        job.document_id = document.id
        service_session.commit()

        logger.info(f"Job {job_id} completed: {len(chunks)} chunks created")

        return {
            "job_id": job_id,
            "document_id": document.id,
            "chunks_created": len(chunks),
            "tokens_used": embedding_result.tokens_used,
        }

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")

        # Update job as failed
        try:
            job = service_session.query(VectorJob).filter(VectorJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.utcnow()
                job.retry_count += 1
                service_session.commit()
        except Exception as update_err:
            logger.error(f"Failed to update job status: {update_err}")

        raise

    finally:
        service_session.close()
        project_session.close()


def fetch_url_content(url: str) -> tuple[str, dict]:
    """
    Fetch and parse content from URL.

    Returns:
        Tuple of (text, metadata)
    """
    import httpx
    from services.parsing import parse_document, detect_format

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "text/plain")
        filename = url.split("/")[-1].split("?")[0]  # Extract filename from URL

        # Parse based on content type
        if "application/pdf" in content_type:
            result = parse_document(
                response.content,
                content_type=content_type,
                filename=filename,
            )
        else:
            result = parse_document(
                response.text,
                content_type=content_type,
                filename=filename,
            )

        return result.text, {
            "title": result.title,
            "word_count": result.word_count,
            "page_count": result.page_count,
            **result.metadata,
        }


def read_file_content(file_path: str) -> tuple[str, dict]:
    """
    Read and parse content from file.

    Returns:
        Tuple of (text, metadata)
    """
    from pathlib import Path
    from services.parsing import parse_document, detect_format

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    filename = Path(file_path).name
    format_type = detect_format(filename=filename)

    # Read file
    if format_type == "pdf":
        with open(file_path, "rb") as f:
            content = f.read()
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

    # Parse
    result = parse_document(content, filename=filename)

    return result.text, {
        "title": result.title,
        "word_count": result.word_count,
        "page_count": result.page_count,
        "file_size": os.path.getsize(file_path),
        **result.metadata,
    }

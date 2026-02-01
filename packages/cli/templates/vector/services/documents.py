"""Documents service for ingestion and management."""

import hashlib
from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.project import Collection, Document, Chunk
from .chunking import get_chunking_service, Chunk as ChunkData
from .embedding import get_embedding_service
from .tokenizer import count_tokens


async def ingest_text(
    session: AsyncSession,
    collection: Collection,
    content: str,
    source_name: str,
    extra_data: Optional[dict] = None,
) -> tuple[Document, int, int]:
    """
    Ingest text content into a collection (synchronous).

    Args:
        session: Database session
        collection: Target collection
        content: Text content to ingest
        source_name: Name identifier for the document
        extra_data: Optional extra_data

    Returns:
        Tuple of (Document, chunks_created, tokens_used)
    """
    # Check token limit
    total_tokens = count_tokens(content)
    if total_tokens > settings.MAX_SYNC_TEXT_TOKENS:
        raise ValueError(
            f"Content exceeds sync limit ({total_tokens} > {settings.MAX_SYNC_TEXT_TOKENS} tokens). "
            "Use async ingestion for large documents."
        )

    # Create content hash for deduplication
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Check for duplicate
    existing = await session.execute(
        select(Document)
        .where(Document.collection_id == collection.id)
        .where(Document.content_hash == content_hash)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Document with identical content already exists in collection")

    # Create document record
    document = Document(
        collection_id=collection.id,
        source_type="text",
        source_name=source_name,
        content_hash=content_hash,
        extra_data=extra_data or {},
    )
    session.add(document)
    await session.flush()  # Get document ID

    # Chunk the content
    chunking_service = get_chunking_service()
    chunk_data_list = chunking_service.chunk_text(
        content,
        source_extra_data={"document_id": document.id},
    )

    if not chunk_data_list:
        raise ValueError("No content to chunk")

    # Generate embeddings
    embedding_service = get_embedding_service()
    texts = [cd.content for cd in chunk_data_list]
    embedding_result = await embedding_service.embed_texts(texts)

    # Create chunk records
    chunks = []
    for i, (chunk_data, embedding) in enumerate(zip(chunk_data_list, embedding_result.embeddings)):
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

    session.add_all(chunks)

    # Update document stats
    document.chunk_count = len(chunks)
    document.token_count = sum(c.token_count for c in chunks)

    # Update collection stats
    collection.document_count += 1
    collection.chunk_count += len(chunks)

    await session.flush()

    return document, len(chunks), embedding_result.tokens_used


async def get_document_by_id(
    session: AsyncSession,
    collection_id: int,
    document_id: int,
) -> Optional[Document]:
    """Get document by ID within a collection."""
    result = await session.execute(
        select(Document)
        .where(Document.collection_id == collection_id)
        .where(Document.id == document_id)
    )
    return result.scalar_one_or_none()


async def list_documents(
    session: AsyncSession,
    collection_id: int,
    limit: int = 50,
    offset: int = 0,
    source_type: Optional[str] = None,
) -> tuple[List[Document], int]:
    """List documents in a collection."""
    query = select(Document).where(Document.collection_id == collection_id)

    if source_type:
        query = query.where(Document.source_type == source_type)

    # Get total count
    count_query = select(func.count()).select_from(
        query.subquery()
    )
    count_result = await session.execute(count_query)
    total = count_result.scalar()

    # Get paginated results
    query = query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    documents = result.scalars().all()

    return list(documents), total


async def delete_document(
    session: AsyncSession,
    document: Document,
    collection: Collection,
) -> int:
    """Delete a document and its chunks."""
    chunks_deleted = document.chunk_count

    # Update collection stats
    collection.document_count -= 1
    collection.chunk_count -= chunks_deleted

    # Delete document (cascades to chunks)
    await session.delete(document)
    await session.flush()

    return chunks_deleted

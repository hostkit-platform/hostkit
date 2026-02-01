"""Semantic search service."""

import time
from typing import Optional, List
from dataclasses import dataclass
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .embedding import get_embedding_service
from .tokenizer import count_tokens


@dataclass
class SearchResultItem:
    """A single search result."""
    chunk_id: int
    content: str
    score: float
    document_id: int
    source_name: str
    extra_data: dict


async def search_collection(
    session: AsyncSession,
    collection_id: int,
    query: str,
    limit: int = 10,
    threshold: float = 0.0,
    include_metadata: bool = True,
    metadata_filter: Optional[dict] = None,
) -> tuple[List[SearchResultItem], int, int]:
    """
    Perform semantic search within a collection.

    Args:
        session: Database session
        collection_id: Collection to search
        query: Search query text
        limit: Maximum results
        threshold: Minimum similarity score (0-1)
        include_metadata: Whether to include chunk metadata
        metadata_filter: Optional metadata filter

    Returns:
        Tuple of (results, query_tokens, search_time_ms)
    """
    start_time = time.time()

    # Get query embedding
    embedding_service = get_embedding_service()
    query_embedding, query_tokens = await embedding_service.embed_single(query)

    # Build search query
    # Using pgvector's cosine distance operator (<=>)
    # Similarity = 1 - distance
    # Note: Using CAST syntax instead of :: to avoid SQLAlchemy parameter parsing issues
    sql = text("""
        SELECT
            c.id AS chunk_id,
            c.content,
            c.extra_data,
            c.document_id,
            d.source_name,
            1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.collection_id = :collection_id
          AND 1 - (c.embedding <=> CAST(:embedding AS vector)) >= :threshold
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)

    # Convert embedding to string format for pgvector
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    result = await session.execute(
        sql,
        {
            "embedding": embedding_str,
            "collection_id": collection_id,
            "threshold": threshold,
            "limit": limit,
        }
    )

    rows = result.fetchall()

    results = []
    for row in rows:
        results.append(SearchResultItem(
            chunk_id=row.chunk_id,
            content=row.content,
            score=round(row.similarity, 4),
            document_id=row.document_id,
            source_name=row.source_name,
            extra_data=row.extra_data if include_metadata else {},
        ))

    search_time_ms = int((time.time() - start_time) * 1000)

    return results, query_tokens, search_time_ms


async def search_all_collections(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    collection_names: Optional[List[str]] = None,
    threshold: float = 0.0,
) -> tuple[List[tuple[str, SearchResultItem]], int, int]:
    """
    Search across multiple collections.

    Args:
        session: Database session
        query: Search query
        limit: Maximum results
        collection_names: Optional list of collection names to search
        threshold: Minimum similarity

    Returns:
        Tuple of (results with collection names, query_tokens, search_time_ms)
    """
    start_time = time.time()

    # Get query embedding
    embedding_service = get_embedding_service()
    query_embedding, query_tokens = await embedding_service.embed_single(query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build query
    if collection_names:
        sql = text("""
            SELECT
                col.name AS collection_name,
                c.id AS chunk_id,
                c.content,
                c.extra_data,
                c.document_id,
                d.source_name,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            JOIN collections col ON c.collection_id = col.id
            WHERE col.name = ANY(:collection_names)
              AND 1 - (c.embedding <=> CAST(:embedding AS vector)) >= :threshold
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)
        params = {
            "embedding": embedding_str,
            "collection_names": collection_names,
            "threshold": threshold,
            "limit": limit,
        }
    else:
        sql = text("""
            SELECT
                col.name AS collection_name,
                c.id AS chunk_id,
                c.content,
                c.extra_data,
                c.document_id,
                d.source_name,
                1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            JOIN collections col ON c.collection_id = col.id
            WHERE 1 - (c.embedding <=> CAST(:embedding AS vector)) >= :threshold
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)
        params = {
            "embedding": embedding_str,
            "threshold": threshold,
            "limit": limit,
        }

    result = await session.execute(sql, params)
    rows = result.fetchall()

    results = []
    for row in rows:
        item = SearchResultItem(
            chunk_id=row.chunk_id,
            content=row.content,
            score=round(row.similarity, 4),
            document_id=row.document_id,
            source_name=row.source_name,
            extra_data=row.extra_data,
        )
        results.append((row.collection_name, item))

    search_time_ms = int((time.time() - start_time) * 1000)

    return results, query_tokens, search_time_ms

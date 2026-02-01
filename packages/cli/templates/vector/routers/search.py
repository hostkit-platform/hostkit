"""Search API router."""

from fastapi import APIRouter, HTTPException, status

from dependencies import ProjectCtx
from schemas.common import SuccessResponse
from schemas.search import (
    SearchRequest,
    SearchAcrossRequest,
    SearchResult,
    SearchResultWithCollection,
    SearchResultDocument,
    SearchResponse,
    SearchAcrossResponse,
)
from services import collections as collection_service
from services import search as search_service

router = APIRouter()


@router.post("/collections/{collection_name}/search")
async def search_collection(
    collection_name: str,
    data: SearchRequest,
    ctx: ProjectCtx,
) -> SuccessResponse:
    """
    Semantic search within a collection.

    Returns chunks ranked by similarity to the query.
    """
    # Get collection
    collection = await collection_service.get_collection_by_name(
        ctx.session, collection_name
    )
    if not collection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Collection '{collection_name}' not found",
                }
            }
        )

    # Perform search
    results, query_tokens, search_time_ms = await search_service.search_collection(
        ctx.session,
        collection.id,
        query=data.query,
        limit=data.limit,
        threshold=data.threshold,
        include_metadata=data.include_metadata,
        metadata_filter=data.filter,
    )

    # Format response
    formatted_results = [
        SearchResult(
            chunk_id=r.chunk_id,
            content=r.content,
            score=r.score,
            document=SearchResultDocument(
                id=r.document_id,
                source_name=r.source_name,
            ),
            metadata=r.extra_data if data.include_metadata else None,
        )
        for r in results
    ]

    return SuccessResponse(
        data=SearchResponse(
            results=formatted_results,
            query_tokens=query_tokens,
            search_time_ms=search_time_ms,
        )
    )


@router.post("/search")
async def search_across_collections(
    data: SearchAcrossRequest,
    ctx: ProjectCtx,
) -> SuccessResponse:
    """
    Search across all collections or specified collections.

    Returns chunks from all collections ranked by similarity.
    """
    results, query_tokens, search_time_ms = await search_service.search_all_collections(
        ctx.session,
        query=data.query,
        limit=data.limit,
        collection_names=data.collections,
        threshold=data.threshold,
    )

    formatted_results = [
        SearchResultWithCollection(
            chunk_id=r.chunk_id,
            content=r.content,
            score=r.score,
            collection=collection_name,
            document=SearchResultDocument(
                id=r.document_id,
                source_name=r.source_name,
            ),
            metadata=r.extra_data,
        )
        for collection_name, r in results
    ]

    return SuccessResponse(
        data=SearchAcrossResponse(
            results=formatted_results,
            query_tokens=query_tokens,
            search_time_ms=search_time_ms,
        )
    )

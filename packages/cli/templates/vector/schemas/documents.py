"""Document schemas."""

from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class DocumentIngestText(BaseModel):
    """Schema for ingesting text content."""
    source_type: Literal["text"] = "text"
    content: str = Field(..., min_length=1)
    source_name: str = Field(..., min_length=1, max_length=1024)
    metadata: Optional[dict] = None
    sync: bool = True  # Text can be sync


class DocumentIngestUrl(BaseModel):
    """Schema for ingesting from URL."""
    source_type: Literal["url"] = "url"
    source: str = Field(..., pattern=r'^https?://')
    metadata: Optional[dict] = None


class DocumentIngestFile(BaseModel):
    """Schema for file upload metadata."""
    metadata: Optional[dict] = None


class DocumentResponse(BaseModel):
    """Schema for document in responses."""
    id: int
    source_type: str
    source_name: str
    source_url: Optional[str]
    chunk_count: int
    token_count: int
    metadata: dict = Field(validation_alias="extra_data")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class DocumentListResponse(BaseModel):
    """Schema for document list response."""
    documents: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class DocumentIngestSyncResponse(BaseModel):
    """Schema for sync ingestion response."""
    document_id: int
    source_name: str
    chunks_created: int
    tokens_used: int


class DocumentIngestAsyncResponse(BaseModel):
    """Schema for async ingestion response."""
    job_id: str
    status: str
    source: str


class DocumentDeleteResponse(BaseModel):
    """Schema for document delete response."""
    chunks_deleted: int

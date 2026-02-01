"""Collection schemas."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    """Schema for creating a collection."""
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9_]*$")
    description: Optional[str] = None


class CollectionUpdate(BaseModel):
    """Schema for updating a collection."""
    description: Optional[str] = None
    metadata: Optional[dict] = Field(default=None, alias="extra_data")


class CollectionResponse(BaseModel):
    """Schema for collection in responses."""
    id: int
    name: str
    description: Optional[str]
    document_count: int
    chunk_count: int
    metadata: dict = Field(validation_alias="extra_data")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class CollectionListResponse(BaseModel):
    """Schema for collection list response."""
    collections: list[CollectionResponse]
    total: int
    limit: int
    offset: int


class CollectionDeleteResponse(BaseModel):
    """Schema for collection delete response."""
    documents_deleted: int
    chunks_deleted: int

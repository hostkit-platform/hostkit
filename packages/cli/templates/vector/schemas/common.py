"""Common Pydantic schemas."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class SuccessResponse(BaseModel):
    """Standard success response wrapper."""
    success: bool = True
    data: Any
    message: Optional[str] = None


class ErrorDetail(BaseModel):
    """Error detail structure."""
    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    """Standard error response wrapper."""
    success: bool = False
    error: ErrorDetail


class PaginationParams(BaseModel):
    """Pagination parameters."""
    limit: int = 50
    offset: int = 0


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    total: int
    limit: int
    offset: int

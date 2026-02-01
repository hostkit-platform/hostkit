"""Common response schemas."""

from typing import Any, Generic, TypeVar
from pydantic import BaseModel
from datetime import datetime

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Error detail structure."""
    code: str
    message: str
    suggestion: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = False
    error: ErrorDetail


class SuccessResponse(BaseModel, Generic[T]):
    """Standard success response."""
    success: bool = True
    data: T
    message: str | None = None
    timestamp: datetime = datetime.utcnow()

"""Health check endpoint for SMS service."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/sms", tags=["health"])


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "sms",
    }

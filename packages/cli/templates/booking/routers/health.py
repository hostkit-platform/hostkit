"""Health check endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from config import settings

router = APIRouter(prefix="/api/booking", tags=["health"])


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint."""
    # Test database connection
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "booking",
        "project": settings.project_name,
        "database": db_status,
    }

"""Health check router."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint."""
    # Check database connection
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "success": True,
        "data": {
            "status": "healthy" if db_status == "connected" else "degraded",
            "database": db_status,
            "service": "hostkit-claude-daemon",
            "version": "1.0.0",
        }
    }

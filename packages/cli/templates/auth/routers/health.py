"""Health check endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db

router = APIRouter(tags=["health"])


@router.get(
    "/auth/health",
    summary="Health check",
)
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check auth service health.

    - Verifies database connectivity
    - Returns service status
    """
    settings = get_settings()

    # Check database connection
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "auth",
        "project": settings.project_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": db_status,
        },
    }


@router.get(
    "/health",
    summary="Simple health check",
)
async def simple_health() -> dict:
    """Simple health check (no dependencies).

    Used for load balancer health checks.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

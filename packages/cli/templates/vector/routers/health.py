"""Health check endpoint."""

from fastapi import APIRouter
from sqlalchemy import text

from config import settings
from database import get_service_session

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint (no authentication required)."""
    services = {
        "database": "unknown",
        "redis": "unknown",
        "openai": "unknown",
    }

    # Check database
    try:
        async for session in get_service_session():
            await session.execute(text("SELECT 1"))
            services["database"] = "connected"
    except Exception as e:
        services["database"] = f"error: {str(e)}"

    # Check Redis
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        services["redis"] = "connected"
    except Exception as e:
        services["redis"] = f"error: {str(e)}"

    # Check OpenAI (just verify key is configured)
    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.startswith("sk-"):
        services["openai"] = "configured"
    else:
        services["openai"] = "not configured"

    # Determine overall status
    status = "healthy"
    if services["database"] != "connected":
        status = "unhealthy"

    return {
        "status": status,
        "version": "1.0.0",
        "services": services,
    }

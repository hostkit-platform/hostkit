"""Health check endpoints."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get(
    "/auth/health",
    summary="Health check with configuration validation",
)
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check auth service health and configuration.

    Verifies:
    - Database connectivity
    - JWT key configuration
    - OAuth configuration (if enabled)
    - Email/SMTP configuration (if enabled)
    - Returns service status
    """
    settings = get_settings()

    # Check database connection
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    checks = {"database": db_status}
    warnings = []
    overall_status = "ok" if db_status == "ok" else "degraded"

    # Check JWT keys
    if not settings.jwt_private_key_path or not settings.jwt_public_key_path:
        warnings.append("JWT keys not configured")
        checks["jwt"] = "warning"
    else:
        private_path = Path(settings.jwt_private_key_path)
        public_path = Path(settings.jwt_public_key_path)
        if not private_path.exists() or not public_path.exists():
            warnings.append("JWT key files missing")
            checks["jwt"] = "warning"
            overall_status = "degraded"
        else:
            checks["jwt"] = "ok"

    # Check OAuth configuration (if enabled)
    if settings.google_enabled:
        if not settings.google_client_secret:
            warnings.append("Google OAuth enabled but client secret not set")
            checks["oauth_google"] = "warning"
            overall_status = "degraded"
        else:
            checks["oauth_google"] = "ok"

    if settings.apple_enabled:
        if not settings.apple_private_key_content:
            warnings.append("Apple Sign-In enabled but private key not set")
            checks["oauth_apple"] = "warning"
            overall_status = "degraded"
        else:
            checks["oauth_apple"] = "ok"

    # Check email configuration (if enabled)
    if settings.email_enabled:
        # Note: Detailed SMTP config check requires EmailService import
        # For now, we just note that email is enabled
        warnings.append("Email enabled - verify SMTP configuration")
        checks["email"] = "warning"

    return {
        "status": overall_status,
        "service": "auth",
        "project": settings.project_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "warnings": warnings if warnings else None,
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

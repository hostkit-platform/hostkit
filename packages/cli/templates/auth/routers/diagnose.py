"""Diagnostic endpoints for auth service health."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.diagnostic_service import DiagnosticService

router = APIRouter(tags=["diagnostic"])


@router.get(
    "/auth/diagnose",
    summary="Comprehensive auth service diagnostics",
)
async def diagnose(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run comprehensive diagnostics on auth service.

    Checks:
    - Database connectivity and schema
    - JWT key configuration
    - OAuth provider setup
    - Email/SMTP configuration
    - Base URL and CORS

    Returns:
    - overall_health: "healthy", "degraded", or "critical"
    - checks: List of diagnostic check results with suggestions
    - configuration: Safe configuration snapshot (secrets redacted)
    """
    service = DiagnosticService()
    result = await service.run_diagnostics(db)
    return result.to_dict()

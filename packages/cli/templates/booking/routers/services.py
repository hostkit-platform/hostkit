"""Services endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_config_id

router = APIRouter()


@router.get("/services")
async def get_services(
    provider_id: Optional[str] = Query(None, description="Filter to provider's services"),
    duration: Optional[int] = Query(None, description="Filter by duration"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get list of active services."""
    query = """
        SELECT DISTINCT s.id, s.name, s.description, s.duration_minutes,
               s.price_cents, s.category, s.sort_order
        FROM services s
        WHERE s.config_id = :config_id AND s.is_active = true
    """
    params = {"config_id": config_id}

    # Filter by provider if specified
    if provider_id:
        query += """
            AND EXISTS (
                SELECT 1 FROM provider_services ps
                WHERE ps.service_id = s.id AND ps.provider_id = :provider_id
            )
        """
        params["provider_id"] = provider_id

    # Filter by duration if specified
    if duration:
        query += " AND s.duration_minutes = :duration"
        params["duration"] = duration

    query += " ORDER BY s.sort_order, s.name"

    results = db.execute(text(query), params).fetchall()

    services = []
    for row in results:
        services.append({
            "id": str(row[0]),
            "name": row[1],
            "description": row[2],
            "duration_minutes": row[3],
            "price_cents": row[4],
            "category": row[5],
        })

    return {"services": services}

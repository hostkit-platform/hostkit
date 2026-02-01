"""Provider endpoints.

Implements:
- Request #7: Provider filtering by service
- Provider schedule management
- Provider appointments view
- Provider exceptions management
"""

from typing import Optional, List
from datetime import date, time
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from dependencies import get_config_id

router = APIRouter()


# =============================================================================
# Request/Response Models for Provider Management
# =============================================================================

class ScheduleEntry(BaseModel):
    """Weekly schedule entry."""
    day_of_week: int  # 0=Monday, 6=Sunday
    start_time: str  # HH:MM format
    end_time: str  # HH:MM format
    is_active: bool = True


class ScheduleUpdate(BaseModel):
    """Request body for updating provider schedule."""
    schedules: List[ScheduleEntry]


class ExceptionEntry(BaseModel):
    """Schedule exception/override entry."""
    date: str  # YYYY-MM-DD format
    is_available: bool = False
    start_time: Optional[str] = None  # HH:MM if custom hours
    end_time: Optional[str] = None  # HH:MM if custom hours
    reason: Optional[str] = None


class ExceptionsUpdate(BaseModel):
    """Request body for updating provider exceptions."""
    exceptions: List[ExceptionEntry]


@router.get("/providers")
async def get_providers(
    service_id: Optional[str] = Query(None, description="Filter to providers who offer this service"),
    is_visible: Optional[bool] = Query(None, description="Filter by visibility in booking flow"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get list of active providers.

    Request #7: Filter by service_id to show only providers who can perform that service.
    """
    query = """
        SELECT DISTINCT
            p.id,
            p.name,
            p.email,
            p.phone,
            p.bio,
            p.avatar_url,
            p.is_visible,
            p.sort_order
        FROM providers p
        WHERE p.config_id = :config_id AND p.is_active = true
    """
    params = {"config_id": config_id}

    # Filter by service (Request #7)
    if service_id:
        query += """
            AND EXISTS (
                SELECT 1 FROM provider_services ps
                WHERE ps.provider_id = p.id AND ps.service_id = :service_id
            )
        """
        params["service_id"] = service_id

    # Filter by visibility
    if is_visible is not None:
        query += " AND p.is_visible = :is_visible"
        params["is_visible"] = is_visible

    query += " ORDER BY p.sort_order, p.name"

    results = db.execute(text(query), params).fetchall()

    providers = []
    for row in results:
        provider_id = str(row[0])

        # Get services this provider offers
        services = db.execute(
            text("""
                SELECT s.id, s.name, s.duration_minutes,
                       COALESCE(ps.price_override_cents, s.price_cents) as price_cents
                FROM services s
                JOIN provider_services ps ON s.id = ps.service_id
                WHERE ps.provider_id = :provider_id AND s.is_active = true
                ORDER BY s.sort_order, s.name
            """),
            {"provider_id": provider_id}
        ).fetchall()

        providers.append({
            "id": provider_id,
            "name": row[1],
            "email": row[2],
            "phone": row[3],
            "bio": row[4],
            "avatar_url": row[5],
            "is_visible": row[6],
            "services": [
                {
                    "id": str(s[0]),
                    "name": s[1],
                    "duration_minutes": s[2],
                    "price_cents": s[3]
                }
                for s in services
            ]
        })

    return {"providers": providers}


@router.get("/providers/{provider_id}")
async def get_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get a specific provider by ID."""
    result = db.execute(
        text("""
            SELECT id, name, email, phone, bio, avatar_url, is_visible, sort_order
            FROM providers
            WHERE id = :provider_id AND config_id = :config_id AND is_active = true
        """),
        {"provider_id": provider_id, "config_id": config_id}
    ).fetchone()

    if not result:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Provider not found")

    # Get services
    services = db.execute(
        text("""
            SELECT s.id, s.name, s.description, s.duration_minutes,
                   COALESCE(ps.price_override_cents, s.price_cents) as price_cents,
                   s.category
            FROM services s
            JOIN provider_services ps ON s.id = ps.service_id
            WHERE ps.provider_id = :provider_id AND s.is_active = true
            ORDER BY s.sort_order, s.name
        """),
        {"provider_id": provider_id}
    ).fetchall()

    # Get schedule
    schedules = db.execute(
        text("""
            SELECT day_of_week, start_time, end_time
            FROM provider_schedules
            WHERE provider_id = :provider_id AND is_active = true
            ORDER BY day_of_week
        """),
        {"provider_id": provider_id}
    ).fetchall()

    return {
        "id": str(result[0]),
        "name": result[1],
        "email": result[2],
        "phone": result[3],
        "bio": result[4],
        "avatar_url": result[5],
        "is_visible": result[6],
        "services": [
            {
                "id": str(s[0]),
                "name": s[1],
                "description": s[2],
                "duration_minutes": s[3],
                "price_cents": s[4],
                "category": s[5]
            }
            for s in services
        ],
        "schedule": [
            {
                "day_of_week": s[0],
                "start_time": s[1].strftime("%H:%M") if hasattr(s[1], 'strftime') else str(s[1])[:5],
                "end_time": s[2].strftime("%H:%M") if hasattr(s[2], 'strftime') else str(s[2])[:5]
            }
            for s in schedules
        ]
    }


# =============================================================================
# Provider Schedule Management
# =============================================================================

@router.get("/providers/{provider_id}/schedule")
async def get_provider_schedule(
    provider_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get provider's weekly schedule."""
    # Verify provider exists and belongs to this config
    provider = db.execute(
        text("SELECT id FROM providers WHERE id = :id AND config_id = :config_id"),
        {"id": provider_id, "config_id": config_id}
    ).fetchone()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    schedules = db.execute(
        text("""
            SELECT day_of_week, start_time, end_time, is_active
            FROM provider_schedules
            WHERE provider_id = :provider_id
            ORDER BY day_of_week
        """),
        {"provider_id": provider_id}
    ).fetchall()

    return {
        "schedules": [
            {
                "day_of_week": s[0],
                "start_time": s[1].strftime("%H:%M") if hasattr(s[1], 'strftime') else str(s[1])[:5],
                "end_time": s[2].strftime("%H:%M") if hasattr(s[2], 'strftime') else str(s[2])[:5],
                "is_active": s[3]
            }
            for s in schedules
        ]
    }


@router.put("/providers/{provider_id}/schedule")
async def update_provider_schedule(
    provider_id: str,
    data: ScheduleUpdate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Update provider's weekly schedule (replaces all existing entries)."""
    # Verify provider exists
    provider = db.execute(
        text("SELECT id FROM providers WHERE id = :id AND config_id = :config_id"),
        {"id": provider_id, "config_id": config_id}
    ).fetchone()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Delete existing schedules
    db.execute(
        text("DELETE FROM provider_schedules WHERE provider_id = :provider_id"),
        {"provider_id": provider_id}
    )

    # Insert new schedules
    for sched in data.schedules:
        db.execute(
            text("""
                INSERT INTO provider_schedules (provider_id, day_of_week, start_time, end_time, is_active)
                VALUES (:provider_id, :day_of_week, :start_time, :end_time, :is_active)
            """),
            {
                "provider_id": provider_id,
                "day_of_week": sched.day_of_week,
                "start_time": sched.start_time,
                "end_time": sched.end_time,
                "is_active": sched.is_active
            }
        )

    db.commit()
    return {"status": "updated", "count": len(data.schedules)}


# =============================================================================
# Provider Appointments View
# =============================================================================

@router.get("/providers/{provider_id}/appointments")
async def get_provider_appointments(
    provider_id: str,
    start: str = Query(..., description="Start date YYYY-MM-DD"),
    end: str = Query(..., description="End date YYYY-MM-DD"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get provider's appointments in a date range."""
    # Verify provider exists
    provider = db.execute(
        text("SELECT id, name FROM providers WHERE id = :id AND config_id = :config_id"),
        {"id": provider_id, "config_id": config_id}
    ).fetchone()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    appointments = db.execute(
        text("""
            SELECT
                a.id,
                a.start_time,
                a.end_time,
                a.duration_minutes,
                a.status,
                a.confirmation_code,
                s.name as service_name,
                c.name as client_name,
                c.email as client_email,
                c.phone as client_phone
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            JOIN customers c ON a.customer_id = c.id
            WHERE a.provider_id = :provider_id
              AND a.start_time >= :start
              AND a.start_time < :end
            ORDER BY a.start_time
        """),
        {"provider_id": provider_id, "start": start, "end": end}
    ).fetchall()

    return {
        "provider_id": provider_id,
        "provider_name": provider[1],
        "appointments": [
            {
                "id": str(a[0]),
                "start_time": a[1].isoformat() if a[1] else None,
                "end_time": a[2].isoformat() if a[2] else None,
                "duration_minutes": a[3],
                "status": a[4],
                "confirmation_code": a[5],
                "service_name": a[6],
                "client_name": a[7],
                "client_email": a[8],
                "client_phone": a[9]
            }
            for a in appointments
        ]
    }


# =============================================================================
# Provider Exceptions Management
# =============================================================================

@router.get("/providers/{provider_id}/exceptions")
async def get_provider_exceptions(
    provider_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get provider's schedule exceptions/overrides."""
    # Verify provider exists
    provider = db.execute(
        text("SELECT id FROM providers WHERE id = :id AND config_id = :config_id"),
        {"id": provider_id, "config_id": config_id}
    ).fetchone()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    exceptions = db.execute(
        text("""
            SELECT override_date, is_available, start_time, end_time, reason
            FROM schedule_overrides
            WHERE provider_id = :provider_id
            ORDER BY override_date
        """),
        {"provider_id": provider_id}
    ).fetchall()

    return {
        "exceptions": [
            {
                "date": e[0].isoformat() if hasattr(e[0], 'isoformat') else str(e[0]),
                "is_available": e[1],
                "start_time": e[2].strftime("%H:%M") if e[2] and hasattr(e[2], 'strftime') else (str(e[2])[:5] if e[2] else None),
                "end_time": e[3].strftime("%H:%M") if e[3] and hasattr(e[3], 'strftime') else (str(e[3])[:5] if e[3] else None),
                "reason": e[4]
            }
            for e in exceptions
        ]
    }


@router.put("/providers/{provider_id}/exceptions")
async def update_provider_exceptions(
    provider_id: str,
    data: ExceptionsUpdate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Update provider's schedule exceptions (replaces all existing entries)."""
    # Verify provider exists
    provider = db.execute(
        text("SELECT id FROM providers WHERE id = :id AND config_id = :config_id"),
        {"id": provider_id, "config_id": config_id}
    ).fetchone()

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Delete existing exceptions
    db.execute(
        text("DELETE FROM schedule_overrides WHERE provider_id = :provider_id"),
        {"provider_id": provider_id}
    )

    # Insert new exceptions
    for exc in data.exceptions:
        db.execute(
            text("""
                INSERT INTO schedule_overrides (provider_id, override_date, is_available, start_time, end_time, reason)
                VALUES (:provider_id, :override_date, :is_available, :start_time, :end_time, :reason)
            """),
            {
                "provider_id": provider_id,
                "override_date": exc.date,
                "is_available": exc.is_available,
                "start_time": exc.start_time,
                "end_time": exc.end_time,
                "reason": exc.reason
            }
        )

    db.commit()
    return {"status": "updated", "count": len(data.exceptions)}

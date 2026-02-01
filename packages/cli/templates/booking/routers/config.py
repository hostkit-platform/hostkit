"""Configuration endpoints.

Implements:
- Request #11: Flow configuration per project
- Request #5: Duration options
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_config_id

router = APIRouter()


@router.get("/config")
async def get_booking_config(
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get booking configuration including flow type.

    Request #11: Returns flow configuration for frontend adaptation.
    """
    result = db.execute(
        text("""
            SELECT
                project_id,
                business_name,
                timezone,
                slot_duration_minutes,
                buffer_minutes,
                min_notice_hours,
                max_advance_days,
                provider_mode_enabled,
                resource_mode_enabled,
                class_mode_enabled,
                flow_type,
                flow_steps,
                allow_any_provider,
                require_payment
            FROM booking_configs
            WHERE id = :config_id
        """),
        {"config_id": config_id}
    ).fetchone()

    if not result:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Booking config not found")

    # Parse flow_steps from JSONB
    flow_steps = result[11] if result[11] else [
        "provider", "date", "duration", "time", "service", "contact", "confirm"
    ]

    return {
        "project_id": result[0],
        "business_name": result[1],
        "timezone": result[2],
        "slot_duration_minutes": result[3],
        "buffer_minutes": result[4],
        "min_notice_hours": result[5],
        "max_advance_days": result[6],
        "modes": {
            "provider": result[7],
            "resource": result[8],
            "class": result[9]
        },
        "flow_type": result[10] or "spa",
        "steps": flow_steps,
        "features": {
            "provider_filter": True,
            "duration_select": True,
            "any_provider": result[12],
            "require_payment": result[13],
            "resource_booking": result[8],
            "class_booking": result[9]
        }
    }


@router.get("/durations")
async def get_durations(
    provider_id: Optional[str] = Query(None, description="Filter to provider's services"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get available duration options.

    Request #5: Returns unique durations from services.
    """
    query = """
        SELECT DISTINCT s.duration_minutes
        FROM services s
        WHERE s.config_id = :config_id AND s.is_active = true
    """
    params = {"config_id": config_id}

    if provider_id:
        query += """
            AND EXISTS (
                SELECT 1 FROM provider_services ps
                WHERE ps.service_id = s.id AND ps.provider_id = :provider_id
            )
        """
        params["provider_id"] = provider_id

    query += " ORDER BY s.duration_minutes"

    results = db.execute(text(query), params).fetchall()

    durations = [row[0] for row in results]

    # Determine default (most common or middle value)
    default = durations[len(durations) // 2] if durations else 60

    return {
        "durations": durations,
        "default": default
    }


@router.get("/categories")
async def get_service_categories(
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get available service categories.

    Request #10: Returns unique categories for filtering.
    """
    results = db.execute(
        text("""
            SELECT DISTINCT category
            FROM services
            WHERE config_id = :config_id
                AND is_active = true
                AND category IS NOT NULL
            ORDER BY category
        """),
        {"config_id": config_id}
    ).fetchall()

    return {
        "categories": [row[0] for row in results]
    }


@router.get("/customer/status")
async def get_customer_status(
    email: str = Query(..., description="Customer email"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Check customer status (new vs returning).

    Request #10: Used for new patient filtering.
    """
    result = db.execute(
        text("""
            SELECT
                id,
                first_appointment_at,
                last_appointment_at,
                appointment_count
            FROM customers
            WHERE config_id = :config_id AND email = :email
        """),
        {"config_id": config_id, "email": email.lower()}
    ).fetchone()

    if not result:
        return {
            "is_new_patient": True,
            "previous_appointments": 0,
            "first_appointment_at": None,
            "last_appointment_at": None
        }

    return {
        "is_new_patient": result[3] == 0,
        "previous_appointments": result[3],
        "first_appointment_at": result[1].isoformat() if result[1] else None,
        "last_appointment_at": result[2].isoformat() if result[2] else None
    }

"""Resource booking endpoints (Phase 3 - Request #8).

For restaurant tables, auto service bays, conference rooms, etc.
"""

from datetime import datetime, date, time, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from database import get_db
from dependencies import get_config_id

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class CustomerInfo(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None


class ResourceBookingRequest(BaseModel):
    resource_id: Optional[str] = None  # None for auto-assign
    resource_type: Optional[str] = None  # If resource_id not specified
    start_time: str  # ISO format
    duration_minutes: int
    party_size: int = 1
    customer: CustomerInfo
    notes: Optional[str] = None


# =============================================================================
# Public Endpoints
# =============================================================================

@router.get("")
async def list_resources(
    resource_type: Optional[str] = Query(None, description="Filter by type (table, bay, room)"),
    capacity_min: Optional[int] = Query(None, description="Minimum capacity"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List available resources.

    Request #8: Filter by type and capacity.
    """
    # Check if resource mode is enabled
    config = db.execute(
        text("SELECT resource_mode_enabled FROM booking_configs WHERE id = :config_id"),
        {"config_id": config_id}
    ).fetchone()

    if not config or not config[0]:
        raise HTTPException(
            status_code=400,
            detail="Resource booking is not enabled for this project"
        )

    query = """
        SELECT id, name, resource_type, description, capacity, attributes
        FROM resources
        WHERE config_id = :config_id AND is_active = true
    """
    params = {"config_id": config_id}

    if resource_type:
        query += " AND resource_type = :resource_type"
        params["resource_type"] = resource_type

    if capacity_min:
        query += " AND capacity >= :capacity_min"
        params["capacity_min"] = capacity_min

    query += " ORDER BY sort_order, name"

    results = db.execute(text(query), params).fetchall()

    return {
        "resources": [
            {
                "id": str(row[0]),
                "name": row[1],
                "resource_type": row[2],
                "description": row[3],
                "capacity": row[4],
                "attributes": row[5] or {}
            }
            for row in results
        ]
    }


@router.get("/availability")
async def get_resource_availability(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    resource_type: Optional[str] = Query(None, description="Filter by type"),
    party_size: int = Query(1, description="Required party size"),
    duration: int = Query(60, description="Duration in minutes"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get available resources for a date.

    Request #8: Shows which resources are available and when.
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    day_of_week = target_date.weekday()

    # Get resources with schedules
    query = """
        SELECT
            r.id,
            r.name,
            r.resource_type,
            r.capacity,
            r.attributes,
            rs.start_time,
            rs.end_time
        FROM resources r
        LEFT JOIN resource_schedules rs ON r.id = rs.resource_id
            AND rs.day_of_week = :day_of_week
            AND rs.is_active = true
        WHERE r.config_id = :config_id
            AND r.is_active = true
            AND r.capacity >= :party_size
    """
    params = {
        "config_id": config_id,
        "day_of_week": day_of_week,
        "party_size": party_size
    }

    if resource_type:
        query += " AND r.resource_type = :resource_type"
        params["resource_type"] = resource_type

    results = db.execute(text(query), params).fetchall()

    available_resources = []

    for row in results:
        resource_id = str(row[0])
        sched_start = row[5]
        sched_end = row[6]

        if not sched_start or not sched_end:
            continue

        # Get existing bookings for this resource
        bookings = db.execute(
            text("""
                SELECT start_time, end_time FROM appointments
                WHERE resource_id = :resource_id
                    AND DATE(start_time) = :target_date
                    AND status != 'cancelled'
                ORDER BY start_time
            """),
            {"resource_id": resource_id, "target_date": target_date}
        ).fetchall()

        booked_ranges = [(b[0], b[1]) for b in bookings]

        # Calculate available slots
        slots = []
        if hasattr(sched_start, 'hour'):
            start_dt = datetime.combine(target_date, sched_start)
            end_dt = datetime.combine(target_date, sched_end)
        else:
            start_dt = datetime.combine(target_date, datetime.strptime(str(sched_start)[:5], "%H:%M").time())
            end_dt = datetime.combine(target_date, datetime.strptime(str(sched_end)[:5], "%H:%M").time())

        current = start_dt
        slot_duration = 30  # Generate 30-min slots

        while current + timedelta(minutes=duration) <= end_dt:
            slot_end = current + timedelta(minutes=duration)

            # Check for conflicts
            is_free = True
            for booked_start, booked_end in booked_ranges:
                if current < booked_end and slot_end > booked_start:
                    is_free = False
                    break

            if is_free:
                slots.append({
                    "start_time": current.isoformat(),
                    "end_time": slot_end.isoformat()
                })

            current += timedelta(minutes=slot_duration)

        if slots:
            available_resources.append({
                "id": resource_id,
                "name": row[1],
                "resource_type": row[2],
                "capacity": row[3],
                "attributes": row[4] or {},
                "available_from": sched_start.strftime("%H:%M") if hasattr(sched_start, 'strftime') else str(sched_start)[:5],
                "available_until": sched_end.strftime("%H:%M") if hasattr(sched_end, 'strftime') else str(sched_end)[:5],
                "slots": slots
            })

    return {
        "date": date,
        "party_size": party_size,
        "duration_minutes": duration,
        "resources": available_resources
    }


@router.post("/book")
async def book_resource(
    data: ResourceBookingRequest,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Book a resource (table, bay, room).

    Request #8: Create appointment with resource_id instead of provider_id.
    """
    # Parse start time
    try:
        start_time = datetime.fromisoformat(data.start_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start_time format")

    end_time = start_time + timedelta(minutes=data.duration_minutes)

    # Find or auto-assign resource
    resource_id = data.resource_id
    if not resource_id:
        resource_id = await _auto_assign_resource(
            db, config_id, data.resource_type, start_time, data.duration_minutes, data.party_size
        )
        if not resource_id:
            raise HTTPException(status_code=409, detail="No resources available for the requested time")

    # Verify resource is available
    conflicts = db.execute(
        text("""
            SELECT id FROM appointments
            WHERE resource_id = :resource_id
                AND status != 'cancelled'
                AND start_time < :end_time
                AND end_time > :start_time
        """),
        {"resource_id": resource_id, "start_time": start_time, "end_time": end_time}
    ).fetchone()

    if conflicts:
        raise HTTPException(status_code=409, detail="Resource is not available at the requested time")

    # Get or create customer
    customer = db.execute(
        text("SELECT id FROM customers WHERE config_id = :config_id AND email = :email"),
        {"config_id": config_id, "email": data.customer.email}
    ).fetchone()

    if customer:
        customer_id = customer[0]
    else:
        result = db.execute(
            text("""
                INSERT INTO customers (config_id, email, name, phone)
                VALUES (:config_id, :email, :name, :phone)
                RETURNING id
            """),
            {
                "config_id": config_id,
                "email": data.customer.email,
                "name": data.customer.name,
                "phone": data.customer.phone
            }
        )
        customer_id = result.fetchone()[0]

    # Create appointment
    result = db.execute(
        text("""
            INSERT INTO appointments (
                config_id, customer_id, resource_id,
                start_time, end_time, duration_minutes,
                party_size, notes
            )
            VALUES (
                :config_id, :customer_id, :resource_id,
                :start_time, :end_time, :duration_minutes,
                :party_size, :notes
            )
            RETURNING id, confirmation_code
        """),
        {
            "config_id": config_id,
            "customer_id": customer_id,
            "resource_id": resource_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": data.duration_minutes,
            "party_size": data.party_size,
            "notes": data.notes
        }
    )
    row = result.fetchone()
    db.commit()

    # Get resource name
    resource = db.execute(
        text("SELECT name, resource_type FROM resources WHERE id = :resource_id"),
        {"resource_id": resource_id}
    ).fetchone()

    return {
        "id": str(row[0]),
        "confirmation_code": row[1],
        "status": "confirmed",
        "resource": {
            "id": resource_id,
            "name": resource[0] if resource else None,
            "type": resource[1] if resource else None
        },
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "party_size": data.party_size
    }


# =============================================================================
# Helper Functions
# =============================================================================

async def _auto_assign_resource(
    db: Session,
    config_id: str,
    resource_type: Optional[str],
    start_time: datetime,
    duration_minutes: int,
    party_size: int
) -> Optional[str]:
    """Auto-assign a resource based on availability and capacity."""
    target_date = start_time.date()
    day_of_week = target_date.weekday()
    end_time = start_time + timedelta(minutes=duration_minutes)

    query = """
        SELECT r.id
        FROM resources r
        JOIN resource_schedules rs ON r.id = rs.resource_id
        WHERE r.config_id = :config_id
            AND r.is_active = true
            AND r.capacity >= :party_size
            AND rs.day_of_week = :day_of_week
            AND rs.is_active = true
            AND :start_time::time >= rs.start_time
            AND :end_time::time <= rs.end_time
            AND NOT EXISTS (
                SELECT 1 FROM appointments a
                WHERE a.resource_id = r.id
                    AND a.status != 'cancelled'
                    AND a.start_time < :end_time
                    AND a.end_time > :start_time
            )
    """
    params = {
        "config_id": config_id,
        "party_size": party_size,
        "day_of_week": day_of_week,
        "start_time": start_time,
        "end_time": end_time
    }

    if resource_type:
        query += " AND r.resource_type = :resource_type"
        params["resource_type"] = resource_type

    # Order by capacity (prefer smallest that fits) then sort_order
    query += " ORDER BY r.capacity, r.sort_order LIMIT 1"

    result = db.execute(text(query), params).fetchone()
    return str(result[0]) if result else None

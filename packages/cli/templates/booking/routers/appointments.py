"""Appointment endpoints.

Implements:
- Request #4: Appointment lookup by confirmation code
- Create, update, cancel appointments
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
import time

from database import get_db
from dependencies import get_config_id

router = APIRouter()


# =============================================================================
# Rate Limiting for by-code lookup (Request #4 security)
# =============================================================================

# Simple in-memory rate limiter
_rate_limit_cache = {}
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # seconds


def check_rate_limit(client_ip: str) -> bool:
    """Check if client is rate limited. Returns True if allowed."""
    now = time.time()
    key = f"appointment_lookup:{client_ip}"

    if key not in _rate_limit_cache:
        _rate_limit_cache[key] = {"count": 1, "window_start": now}
        return True

    entry = _rate_limit_cache[key]

    # Reset window if expired
    if now - entry["window_start"] > RATE_LIMIT_WINDOW:
        _rate_limit_cache[key] = {"count": 1, "window_start": now}
        return True

    # Check count
    if entry["count"] >= RATE_LIMIT_REQUESTS:
        return False

    entry["count"] += 1
    return True


# =============================================================================
# Request/Response Models
# =============================================================================

class CustomerInfo(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None


class CreateAppointmentRequest(BaseModel):
    provider_id: Optional[str] = None  # None for "Any Available"
    service_id: str
    start_time: str  # ISO format
    duration_minutes: int
    customer: CustomerInfo
    notes: Optional[str] = None
    # For resource booking (Phase 3)
    resource_id: Optional[str] = None
    party_size: Optional[int] = 1


class UpdateAppointmentRequest(BaseModel):
    start_time: Optional[str] = None
    provider_id: Optional[str] = None
    notes: Optional[str] = None


# =============================================================================
# Request #4: Appointment Lookup by Confirmation Code
# =============================================================================

@router.get("/by-code/{code}")
async def get_appointment_by_code(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get appointment details by confirmation code.

    Rate limited to prevent code enumeration attacks.
    """
    # Check rate limit
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later."
        )

    result = db.execute(
        text("""
            SELECT
                a.id,
                a.confirmation_code,
                a.status,
                a.start_time,
                a.end_time,
                s.duration_minutes,
                a.price_cents,
                a.payment_status,
                a.notes,
                a.created_at,
                s.id as service_id,
                s.name as service_name,
                s.price_cents as service_price,
                p.id as provider_id,
                p.name as provider_name,
                p.avatar_url as provider_avatar,
                c.id as customer_id,
                c.name as customer_name,
                c.email as customer_email,
                c.phone as customer_phone,
                r.id as room_id,
                r.name as room_name
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            LEFT JOIN providers p ON a.provider_id = p.id
            LEFT JOIN customers c ON a.customer_id = c.id
            LEFT JOIN rooms r ON a.room_id = r.id
            WHERE a.confirmation_code = :code
                AND a.config_id = :config_id
        """),
        {"code": code.upper(), "config_id": config_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return {
        "id": str(result[0]),
        "confirmation_code": result[1],
        "status": result[2],
        "start_time": result[3].isoformat() if result[3] else None,
        "end_time": result[4].isoformat() if result[4] else None,
        "duration_minutes": result[5],
        "price_cents": result[6],
        "payment_status": result[7],
        "notes": result[8],
        "created_at": result[9].isoformat() if result[9] else None,
        "service": {
            "id": str(result[10]),
            "name": result[11],
            "price_cents": result[12]
        } if result[10] else None,
        "provider": {
            "id": str(result[13]),
            "name": result[14],
            "avatar_url": result[15]
        } if result[13] else None,
        "customer": {
            "id": str(result[16]),
            "name": result[17],
            "email": result[18],
            "phone": result[19]
        },
        "room": {
            "id": str(result[20]),
            "name": result[21]
        } if result[20] else None
    }


# =============================================================================
# CRUD Endpoints
# =============================================================================

@router.get("")
async def list_appointments(
    status: Optional[str] = Query(None, description="Filter by status"),
    provider_id: Optional[str] = Query(None, description="Filter by provider"),
    customer_email: Optional[str] = Query(None, description="Filter by customer email"),
    from_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List appointments with filters."""
    query = """
        SELECT
            a.id,
            a.confirmation_code,
            a.status,
            a.start_time,
            a.end_time,
            s.duration_minutes,
            a.price_cents,
            s.name as service_name,
            p.name as provider_name,
            c.name as customer_name,
            c.email as customer_email
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        LEFT JOIN providers p ON a.provider_id = p.id
        LEFT JOIN customers c ON a.customer_id = c.id
        WHERE a.config_id = :config_id
    """
    params = {"config_id": config_id}

    if status:
        query += " AND a.status = :status"
        params["status"] = status

    if provider_id:
        query += " AND a.provider_id = :provider_id"
        params["provider_id"] = provider_id

    if customer_email:
        query += " AND LOWER(c.email) = LOWER(:customer_email)"
        params["customer_email"] = customer_email

    if from_date:
        query += " AND a.start_time >= :from_date"
        params["from_date"] = from_date

    if to_date:
        query += " AND a.start_time < :to_date"
        params["to_date"] = to_date + " 23:59:59"

    query += " ORDER BY a.start_time DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    results = db.execute(text(query), params).fetchall()

    appointments = [
        {
            "id": str(row[0]),
            "confirmation_code": row[1],
            "status": row[2],
            "start_time": row[3].isoformat() if row[3] else None,
            "end_time": row[4].isoformat() if row[4] else None,
            "duration_minutes": row[5],
            "price_cents": row[6],
            "service_name": row[7],
            "provider_name": row[8],
            "customer_name": row[9],
            "customer_email": row[10]
        }
        for row in results
    ]

    return {"appointments": appointments, "limit": limit, "offset": offset}


@router.get("/{appointment_id}")
async def get_appointment(
    appointment_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get appointment by ID."""
    result = db.execute(
        text("""
            SELECT
                a.id,
                a.confirmation_code,
                a.status,
                a.start_time,
                a.end_time,
                s.duration_minutes,
                a.price_cents,
                a.payment_status,
                a.notes,
                a.created_at,
                s.id as service_id,
                s.name as service_name,
                s.price_cents as service_price,
                p.id as provider_id,
                p.name as provider_name,
                p.avatar_url as provider_avatar,
                c.id as customer_id,
                c.name as customer_name,
                c.email as customer_email,
                c.phone as customer_phone
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            LEFT JOIN providers p ON a.provider_id = p.id
            LEFT JOIN customers c ON a.customer_id = c.id
            WHERE a.id = :appointment_id
                AND a.config_id = :config_id
        """),
        {"appointment_id": appointment_id, "config_id": config_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return {
        "id": str(result[0]),
        "confirmation_code": result[1],
        "status": result[2],
        "start_time": result[3].isoformat() if result[3] else None,
        "end_time": result[4].isoformat() if result[4] else None,
        "duration_minutes": result[5],
        "price_cents": result[6],
        "payment_status": result[7],
        "notes": result[8],
        "created_at": result[9].isoformat() if result[9] else None,
        "service": {
            "id": str(result[10]),
            "name": result[11],
            "price_cents": result[12]
        } if result[10] else None,
        "provider": {
            "id": str(result[13]),
            "name": result[14],
            "avatar_url": result[15]
        } if result[13] else None,
        "customer": {
            "id": str(result[16]),
            "name": result[17],
            "email": result[18],
            "phone": result[19]
        }
    }


@router.post("")
async def create_appointment(
    data: CreateAppointmentRequest,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Create a new appointment."""
    # Parse start time
    try:
        start_time = datetime.fromisoformat(data.start_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start_time format")

    end_time = start_time + timedelta(minutes=data.duration_minutes)

    # Get or create customer
    customer = db.execute(
        text("""
            SELECT id FROM customers
            WHERE config_id = :config_id AND email = :email
        """),
        {"config_id": config_id, "email": data.customer.email}
    ).fetchone()

    if customer:
        customer_id = customer[0]
        # Update customer info
        db.execute(
            text("""
                UPDATE customers SET name = :name, phone = :phone
                WHERE id = :customer_id
            """),
            {
                "customer_id": customer_id,
                "name": data.customer.name,
                "phone": data.customer.phone
            }
        )
    else:
        # Create new customer
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

    # Get service price
    service = db.execute(
        text("SELECT price_cents FROM services WHERE id = :service_id"),
        {"service_id": data.service_id}
    ).fetchone()

    price_cents = service[0] if service else 0

    # Handle provider assignment
    provider_id = data.provider_id
    if not provider_id:
        # Auto-assign provider (round-robin)
        provider_id = await _auto_assign_provider(
            db, config_id, data.service_id, start_time, data.duration_minutes
        )

    # Create appointment
    result = db.execute(
        text("""
            INSERT INTO appointments (
                config_id, customer_id, provider_id, service_id,
                start_time, end_time, duration_minutes, price_cents,
                notes, resource_id, party_size
            )
            VALUES (
                :config_id, :customer_id, :provider_id, :service_id,
                :start_time, :end_time, :duration_minutes, :price_cents,
                :notes, :resource_id, :party_size
            )
            RETURNING id, confirmation_code
        """),
        {
            "config_id": config_id,
            "customer_id": customer_id,
            "provider_id": provider_id,
            "service_id": data.service_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": data.duration_minutes,
            "price_cents": price_cents,
            "notes": data.notes,
            "resource_id": data.resource_id,
            "party_size": data.party_size or 1
        }
    )
    row = result.fetchone()
    db.commit()

    return {
        "id": str(row[0]),
        "confirmation_code": row[1],
        "status": "confirmed",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "customer_email": data.customer.email
    }


@router.patch("/{appointment_id}")
async def update_appointment(
    appointment_id: str,
    data: UpdateAppointmentRequest,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Update an appointment."""
    # Verify appointment exists
    existing = db.execute(
        text("""
            SELECT id, start_time, duration_minutes
            FROM appointments
            WHERE id = :appointment_id AND config_id = :config_id
        """),
        {"appointment_id": appointment_id, "config_id": config_id}
    ).fetchone()

    if not existing:
        raise HTTPException(status_code=404, detail="Appointment not found")

    updates = []
    params = {"appointment_id": appointment_id}

    if data.start_time:
        try:
            new_start = datetime.fromisoformat(data.start_time.replace("Z", "+00:00"))
            new_end = new_start + timedelta(minutes=existing[2])
            updates.append("start_time = :start_time")
            updates.append("end_time = :end_time")
            params["start_time"] = new_start
            params["end_time"] = new_end
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_time format")

    if data.provider_id:
        updates.append("provider_id = :provider_id")
        params["provider_id"] = data.provider_id

    if data.notes is not None:
        updates.append("notes = :notes")
        params["notes"] = data.notes

    if updates:
        query = f"UPDATE appointments SET {', '.join(updates)} WHERE id = :appointment_id"
        db.execute(text(query), params)
        db.commit()

    return {"updated": True, "id": appointment_id}


@router.post("/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Cancel an appointment."""
    result = db.execute(
        text("""
            UPDATE appointments
            SET status = 'cancelled',
                cancelled_at = CURRENT_TIMESTAMP,
                cancellation_reason = :reason
            WHERE id = :appointment_id AND config_id = :config_id
            RETURNING confirmation_code
        """),
        {
            "appointment_id": appointment_id,
            "config_id": config_id,
            "reason": reason
        }
    )
    row = result.fetchone()
    db.commit()

    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return {
        "cancelled": True,
        "confirmation_code": row[0]
    }


# =============================================================================
# Helper Functions
# =============================================================================

async def _auto_assign_provider(
    db: Session,
    config_id: str,
    service_id: str,
    start_time: datetime,
    duration_minutes: int
) -> Optional[str]:
    """Auto-assign a provider using round-robin based on daily appointment count."""
    target_date = start_time.date()
    day_of_week = target_date.weekday()
    end_time = start_time + timedelta(minutes=duration_minutes)

    # Find providers who can do this service and are available at this time
    available_providers = db.execute(
        text("""
            SELECT p.id
            FROM providers p
            JOIN provider_services ps ON p.id = ps.provider_id
            JOIN provider_schedules sched ON p.id = sched.provider_id
            LEFT JOIN schedule_overrides so ON p.id = so.provider_id
                AND so.override_date = :target_date
            WHERE p.config_id = :config_id
                AND p.is_active = true
                AND ps.service_id = :service_id
                AND sched.day_of_week = :day_of_week
                AND sched.is_active = true
                AND COALESCE(so.is_available, true) = true
                AND :start_time::time >= COALESCE(so.start_time, sched.start_time)
                AND :end_time::time <= COALESCE(so.end_time, sched.end_time)
                AND NOT EXISTS (
                    SELECT 1 FROM appointments a
                    WHERE a.provider_id = p.id
                        AND a.status != 'cancelled'
                        AND a.start_time < :end_time
                        AND a.end_time > :start_time
                )
        """),
        {
            "config_id": config_id,
            "service_id": service_id,
            "day_of_week": day_of_week,
            "target_date": target_date,
            "start_time": start_time,
            "end_time": end_time
        }
    ).fetchall()

    if not available_providers:
        return None

    # Get appointment counts for today to load balance
    provider_ids = [str(p[0]) for p in available_providers]

    # Pick provider with fewest appointments today
    counts = db.execute(
        text("""
            SELECT provider_id, COUNT(*) as cnt
            FROM appointments
            WHERE provider_id = ANY(:provider_ids)
                AND DATE(start_time) = :target_date
                AND status != 'cancelled'
            GROUP BY provider_id
        """),
        {"provider_ids": provider_ids, "target_date": target_date}
    ).fetchall()

    count_map = {str(row[0]): row[1] for row in counts}

    # Find provider with minimum appointments
    min_count = float('inf')
    selected_provider = provider_ids[0]

    for pid in provider_ids:
        cnt = count_map.get(pid, 0)
        if cnt < min_count:
            min_count = cnt
            selected_provider = pid

    return selected_provider

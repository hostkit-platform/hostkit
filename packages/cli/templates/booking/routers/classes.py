"""Class booking endpoints (Phase 3 - Request #9).

For fitness classes, workshops, tours, cooking classes, etc.
"""

from datetime import datetime, date, timedelta
from typing import Optional
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


class ClassBookingRequest(BaseModel):
    class_schedule_id: str
    spots: int = 1
    customer: CustomerInfo
    notes: Optional[str] = None


# =============================================================================
# Public Endpoints
# =============================================================================

@router.get("")
async def list_classes(
    category: Optional[str] = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List available class types.

    Request #9: Returns class types with instructor and capacity info.
    """
    # Check if class mode is enabled
    config = db.execute(
        text("SELECT class_mode_enabled FROM booking_configs WHERE id = :config_id"),
        {"config_id": config_id}
    ).fetchone()

    if not config or not config[0]:
        raise HTTPException(
            status_code=400,
            detail="Class booking is not enabled for this project"
        )

    query = """
        SELECT c.id, c.name, c.description, c.duration_minutes, c.capacity,
               c.price_cents, c.category,
               p.id as instructor_id, p.name as instructor_name, p.avatar_url
        FROM classes c
        LEFT JOIN providers p ON c.instructor_id = p.id
        WHERE c.config_id = :config_id AND c.is_active = true
    """
    params = {"config_id": config_id}

    if category:
        query += " AND c.category = :category"
        params["category"] = category

    query += " ORDER BY c.sort_order, c.name"

    results = db.execute(text(query), params).fetchall()

    return {
        "classes": [
            {
                "id": str(row[0]),
                "name": row[1],
                "description": row[2],
                "duration_minutes": row[3],
                "capacity": row[4],
                "price_cents": row[5],
                "category": row[6],
                "instructor": {
                    "id": str(row[7]),
                    "name": row[8],
                    "avatar_url": row[9]
                } if row[7] else None
            }
            for row in results
        ]
    }


@router.get("/schedule")
async def get_class_schedule(
    date: Optional[str] = Query(None, description="Specific date (YYYY-MM-DD)"),
    from_date: Optional[str] = Query(None, description="Start date range"),
    to_date: Optional[str] = Query(None, description="End date range"),
    class_id: Optional[str] = Query(None, description="Filter to specific class type"),
    category: Optional[str] = Query(None, description="Filter by category"),
    show_full: bool = Query(False, description="Include full classes"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get scheduled class instances.

    Request #9: Shows upcoming classes with spots remaining.
    """
    query = """
        SELECT
            cs.id,
            cs.start_time,
            cs.end_time,
            cs.spots_remaining,
            cs.status,
            COALESCE(cs.capacity_override, c.capacity) as capacity,
            COALESCE(cs.price_override_cents, c.price_cents) as price_cents,
            c.id as class_id,
            c.name as class_name,
            c.description,
            c.duration_minutes,
            c.category,
            COALESCE(p2.id, p.id) as instructor_id,
            COALESCE(p2.name, p.name) as instructor_name,
            COALESCE(p2.avatar_url, p.avatar_url) as instructor_avatar
        FROM class_schedules cs
        JOIN classes c ON cs.class_id = c.id
        LEFT JOIN providers p ON c.instructor_id = p.id
        LEFT JOIN providers p2 ON cs.instructor_override_id = p2.id
        WHERE c.config_id = :config_id
            AND cs.status != 'cancelled'
    """
    params = {"config_id": config_id}

    # Date filtering
    if date:
        query += " AND DATE(cs.start_time) = :date"
        params["date"] = date
    else:
        if from_date:
            query += " AND cs.start_time >= :from_date"
            params["from_date"] = from_date
        else:
            # Default to from today
            query += " AND cs.start_time >= :now"
            params["now"] = datetime.now()

        if to_date:
            query += " AND cs.start_time < :to_date"
            params["to_date"] = to_date + " 23:59:59"

    if class_id:
        query += " AND c.id = :class_id"
        params["class_id"] = class_id

    if category:
        query += " AND c.category = :category"
        params["category"] = category

    if not show_full:
        query += " AND cs.status = 'open' AND cs.spots_remaining > 0"

    query += " ORDER BY cs.start_time"

    results = db.execute(text(query), params).fetchall()

    return {
        "schedules": [
            {
                "id": str(row[0]),
                "start_time": row[1].isoformat() if row[1] else None,
                "end_time": row[2].isoformat() if row[2] else None,
                "spots_remaining": row[3],
                "status": row[4],
                "capacity": row[5],
                "price_cents": row[6],
                "class": {
                    "id": str(row[7]),
                    "name": row[8],
                    "description": row[9],
                    "duration_minutes": row[10],
                    "category": row[11]
                },
                "instructor": {
                    "id": str(row[12]),
                    "name": row[13],
                    "avatar_url": row[14]
                } if row[12] else None
            }
            for row in results
        ]
    }


@router.get("/schedule/{schedule_id}")
async def get_class_schedule_detail(
    schedule_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get details for a specific scheduled class."""
    result = db.execute(
        text("""
            SELECT
                cs.id,
                cs.start_time,
                cs.end_time,
                cs.spots_remaining,
                cs.status,
                COALESCE(cs.capacity_override, c.capacity) as capacity,
                COALESCE(cs.price_override_cents, c.price_cents) as price_cents,
                c.id as class_id,
                c.name as class_name,
                c.description,
                c.duration_minutes,
                c.category,
                COALESCE(p2.id, p.id) as instructor_id,
                COALESCE(p2.name, p.name) as instructor_name,
                COALESCE(p2.avatar_url, p.avatar_url) as instructor_avatar,
                COALESCE(p2.bio, p.bio) as instructor_bio
            FROM class_schedules cs
            JOIN classes c ON cs.class_id = c.id
            LEFT JOIN providers p ON c.instructor_id = p.id
            LEFT JOIN providers p2 ON cs.instructor_override_id = p2.id
            WHERE cs.id = :schedule_id
                AND c.config_id = :config_id
        """),
        {"schedule_id": schedule_id, "config_id": config_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Class schedule not found")

    return {
        "id": str(result[0]),
        "start_time": result[1].isoformat() if result[1] else None,
        "end_time": result[2].isoformat() if result[2] else None,
        "spots_remaining": result[3],
        "status": result[4],
        "capacity": result[5],
        "price_cents": result[6],
        "class": {
            "id": str(result[7]),
            "name": result[8],
            "description": result[9],
            "duration_minutes": result[10],
            "category": result[11]
        },
        "instructor": {
            "id": str(result[12]),
            "name": result[13],
            "avatar_url": result[14],
            "bio": result[15]
        } if result[12] else None
    }


@router.post("/book")
async def book_class(
    data: ClassBookingRequest,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Book spots in a class.

    Request #9: Create class booking with capacity enforcement.
    """
    # Get class schedule and verify availability
    schedule = db.execute(
        text("""
            SELECT cs.id, cs.spots_remaining, cs.status,
                   COALESCE(cs.price_override_cents, c.price_cents) as price_cents,
                   c.name as class_name, c.config_id
            FROM class_schedules cs
            JOIN classes c ON cs.class_id = c.id
            WHERE cs.id = :schedule_id
        """),
        {"schedule_id": data.class_schedule_id}
    ).fetchone()

    if not schedule:
        raise HTTPException(status_code=404, detail="Class schedule not found")

    if schedule[5] != config_id:
        raise HTTPException(status_code=404, detail="Class schedule not found")

    if schedule[2] == 'cancelled':
        raise HTTPException(status_code=400, detail="This class has been cancelled")

    if schedule[2] == 'full' or schedule[1] < data.spots:
        raise HTTPException(
            status_code=409,
            detail=f"Not enough spots available. Only {schedule[1]} spots remaining."
        )

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

    # Check if customer already booked this class
    existing = db.execute(
        text("""
            SELECT id FROM class_bookings
            WHERE class_schedule_id = :schedule_id
                AND customer_id = :customer_id
                AND status != 'cancelled'
        """),
        {"schedule_id": data.class_schedule_id, "customer_id": customer_id}
    ).fetchone()

    if existing:
        raise HTTPException(status_code=409, detail="You have already booked this class")

    # Create booking (trigger will update spots_remaining)
    price_cents = schedule[3] * data.spots if schedule[3] else 0

    result = db.execute(
        text("""
            INSERT INTO class_bookings (
                class_schedule_id, customer_id, spots_booked, price_cents
            )
            VALUES (
                :schedule_id, :customer_id, :spots, :price_cents
            )
            RETURNING id, confirmation_code
        """),
        {
            "schedule_id": data.class_schedule_id,
            "customer_id": customer_id,
            "spots": data.spots,
            "price_cents": price_cents
        }
    )
    row = result.fetchone()
    db.commit()

    return {
        "id": str(row[0]),
        "confirmation_code": row[1],
        "status": "confirmed",
        "class_name": schedule[4],
        "spots_booked": data.spots,
        "price_cents": price_cents,
        "customer_email": data.customer.email
    }


@router.get("/bookings/by-code/{code}")
async def get_class_booking_by_code(
    code: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get class booking by confirmation code."""
    result = db.execute(
        text("""
            SELECT
                cb.id,
                cb.confirmation_code,
                cb.spots_booked,
                cb.status,
                cb.price_cents,
                cb.payment_status,
                cb.created_at,
                cs.start_time,
                cs.end_time,
                c.name as class_name,
                c.description,
                c.category,
                COALESCE(p2.name, p.name) as instructor_name,
                cust.name as customer_name,
                cust.email as customer_email
            FROM class_bookings cb
            JOIN class_schedules cs ON cb.class_schedule_id = cs.id
            JOIN classes c ON cs.class_id = c.id
            LEFT JOIN providers p ON c.instructor_id = p.id
            LEFT JOIN providers p2 ON cs.instructor_override_id = p2.id
            JOIN customers cust ON cb.customer_id = cust.id
            WHERE cb.confirmation_code = :code
                AND c.config_id = :config_id
        """),
        {"code": code.upper(), "config_id": config_id}
    ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Booking not found")

    return {
        "id": str(result[0]),
        "confirmation_code": result[1],
        "spots_booked": result[2],
        "status": result[3],
        "price_cents": result[4],
        "payment_status": result[5],
        "created_at": result[6].isoformat() if result[6] else None,
        "class": {
            "start_time": result[7].isoformat() if result[7] else None,
            "end_time": result[8].isoformat() if result[8] else None,
            "name": result[9],
            "description": result[10],
            "category": result[11],
            "instructor_name": result[12]
        },
        "customer": {
            "name": result[13],
            "email": result[14]
        }
    }


@router.post("/bookings/{booking_id}/cancel")
async def cancel_class_booking(
    booking_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Cancel a class booking.

    Releases spots back to the class schedule.
    """
    # Verify booking exists and belongs to this config
    booking = db.execute(
        text("""
            SELECT cb.id, c.config_id
            FROM class_bookings cb
            JOIN class_schedules cs ON cb.class_schedule_id = cs.id
            JOIN classes c ON cs.class_id = c.id
            WHERE cb.id = :booking_id
        """),
        {"booking_id": booking_id}
    ).fetchone()

    if not booking or booking[1] != config_id:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Update status (trigger will restore spots)
    result = db.execute(
        text("""
            UPDATE class_bookings
            SET status = 'cancelled',
                cancelled_at = CURRENT_TIMESTAMP,
                cancellation_reason = :reason
            WHERE id = :booking_id
            RETURNING confirmation_code
        """),
        {"booking_id": booking_id, "reason": reason}
    )
    row = result.fetchone()
    db.commit()

    return {
        "cancelled": True,
        "confirmation_code": row[0] if row else None
    }

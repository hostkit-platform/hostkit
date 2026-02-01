"""Availability endpoints - Core booking availability logic.

Implements:
- Request #1: Duration-aware slots
- Request #2: Provider time range per date
- Request #6: Calendar availability indicators
"""

from datetime import datetime, date, time, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from dependencies import get_config_id

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class ProviderAvailability(BaseModel):
    """Provider with availability window for a date."""
    id: str
    name: str
    avatar_url: Optional[str] = None
    available_from: str  # HH:MM
    available_until: str  # HH:MM
    booked_slots: int = 0


class DateAvailability(BaseModel):
    """Availability info for a calendar date."""
    date: str  # YYYY-MM-DD
    slots_available: int
    status: str  # available, limited, full


class TimeSlot(BaseModel):
    """Available time slot."""
    start_time: str  # ISO timestamp
    end_time: str  # ISO timestamp
    providers: List[str]  # Provider IDs available at this time
    provider_names: List[str]
    max_duration_minutes: int  # Longest appointment that fits


# =============================================================================
# Request #6: Calendar Availability Indicators
# =============================================================================

@router.get("/dates")
async def get_available_dates(
    month: str = Query(..., description="Month in YYYY-MM format", regex=r"^\d{4}-\d{2}$"),
    provider_id: Optional[str] = Query(None, description="Filter to specific provider"),
    duration: Optional[int] = Query(None, description="Filter to dates with slots >= duration"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get available dates for a month with availability indicators.

    Returns dates with slot counts and status (available/limited/full).
    """
    # Parse month
    try:
        year, month_num = map(int, month.split("-"))
        start_date = date(year, month_num, 1)
        # Get last day of month
        if month_num == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month_num + 1, 1) - timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid month format")

    # Get booking config for slot duration
    config_result = db.execute(
        text("SELECT slot_duration_minutes, min_notice_hours FROM booking_configs WHERE id = :config_id"),
        {"config_id": config_id}
    ).fetchone()

    if not config_result:
        raise HTTPException(status_code=404, detail="Booking config not found")

    slot_duration = config_result[0] or 30
    min_notice_hours = config_result[1] or 1

    # Calculate minimum bookable time
    min_bookable_time = datetime.now() + timedelta(hours=min_notice_hours)

    dates_result = []
    current_date = start_date

    while current_date <= end_date:
        # Skip dates before minimum notice
        if datetime.combine(current_date, time(23, 59)) < min_bookable_time:
            current_date += timedelta(days=1)
            continue

        # Get available slots for this date
        slots = await _calculate_slots_for_date(
            db, config_id, current_date, provider_id, duration, slot_duration
        )

        if slots > 0:
            # Determine status based on slot count
            if slots > 4:
                status = "available"
            elif slots > 0:
                status = "limited"
            else:
                status = "full"

            dates_result.append({
                "date": current_date.isoformat(),
                "slots_available": slots,
                "status": status
            })

        current_date += timedelta(days=1)

    return {"dates": dates_result}


# =============================================================================
# Request #2: Provider Time Range Per Date
# =============================================================================

@router.get("/providers")
async def get_provider_availability(
    date: str = Query(..., description="Date in YYYY-MM-DD format", regex=r"^\d{4}-\d{2}-\d{2}$"),
    service_id: Optional[str] = Query(None, description="Filter to providers who offer this service"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get provider availability windows for a specific date.

    Returns each provider's available time range and booking load.
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    day_of_week = target_date.weekday()  # 0=Monday, 6=Sunday

    # Build provider query
    query = """
        SELECT DISTINCT
            p.id,
            p.name,
            p.avatar_url,
            COALESCE(so.start_time, ps.start_time) as start_time,
            COALESCE(so.end_time, ps.end_time) as end_time,
            COALESCE(so.is_available, true) as is_available,
            p.sort_order
        FROM providers p
        LEFT JOIN provider_schedules ps ON p.id = ps.provider_id
            AND ps.day_of_week = :day_of_week
            AND ps.is_active = true
        LEFT JOIN schedule_overrides so ON p.id = so.provider_id
            AND so.override_date = :target_date
        WHERE p.config_id = :config_id
            AND p.is_active = true
    """
    params = {
        "config_id": config_id,
        "day_of_week": day_of_week,
        "target_date": target_date
    }

    # Filter by service if specified
    if service_id:
        query += """
            AND EXISTS (
                SELECT 1 FROM provider_services ps2
                WHERE ps2.provider_id = p.id AND ps2.service_id = :service_id
            )
        """
        params["service_id"] = service_id

    query += " ORDER BY p.sort_order, p.name"

    results = db.execute(text(query), params).fetchall()

    providers = []
    for row in results:
        provider_id = str(row[0])
        is_available = row[5]
        start_time = row[3]
        end_time = row[4]

        # Skip if not available this date
        if not is_available or not start_time or not end_time:
            continue

        # Count booked slots for this provider on this date
        booked_count = db.execute(
            text("""
                SELECT COUNT(*) FROM appointments
                WHERE provider_id = :provider_id
                    AND DATE(start_time) = :target_date
                    AND status != 'cancelled'
            """),
            {"provider_id": provider_id, "target_date": target_date}
        ).scalar() or 0

        providers.append({
            "id": provider_id,
            "name": row[1],
            "avatar_url": row[2],
            "available_from": start_time.strftime("%H:%M") if hasattr(start_time, 'strftime') else str(start_time)[:5],
            "available_until": end_time.strftime("%H:%M") if hasattr(end_time, 'strftime') else str(end_time)[:5],
            "booked_slots": booked_count
        })

    return {"providers": providers}


# =============================================================================
# Request #1: Duration-Aware Availability Slots
# =============================================================================

@router.get("/slots")
async def get_available_slots(
    date: str = Query(..., description="Date in YYYY-MM-DD format", regex=r"^\d{4}-\d{2}-\d{2}$"),
    provider_id: Optional[str] = Query(None, description="Filter to specific provider (or 'any')"),
    duration: Optional[int] = Query(None, description="Required duration in minutes"),
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Get available time slots for a date.

    If duration is specified, only returns slots where that duration fits.
    """
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    # Get config
    config = db.execute(
        text("""
            SELECT slot_duration_minutes, buffer_minutes, min_notice_hours
            FROM booking_configs WHERE id = :config_id
        """),
        {"config_id": config_id}
    ).fetchone()

    if not config:
        raise HTTPException(status_code=404, detail="Booking config not found")

    slot_duration = config[0] or 30
    buffer_minutes = config[1] or 0
    min_notice_hours = config[2] or 1

    # Calculate minimum bookable time
    min_bookable_time = datetime.now() + timedelta(hours=min_notice_hours)

    day_of_week = target_date.weekday()

    # Get providers with their schedules
    provider_filter = ""
    params = {
        "config_id": config_id,
        "day_of_week": day_of_week,
        "target_date": target_date
    }

    if provider_id and provider_id != "any":
        provider_filter = "AND p.id = :provider_id"
        params["provider_id"] = provider_id

    providers_query = f"""
        SELECT
            p.id,
            p.name,
            COALESCE(so.start_time, ps.start_time) as start_time,
            COALESCE(so.end_time, ps.end_time) as end_time,
            COALESCE(so.is_available, true) as is_available
        FROM providers p
        LEFT JOIN provider_schedules ps ON p.id = ps.provider_id
            AND ps.day_of_week = :day_of_week
            AND ps.is_active = true
        LEFT JOIN schedule_overrides so ON p.id = so.provider_id
            AND so.override_date = :target_date
        WHERE p.config_id = :config_id
            AND p.is_active = true
            {provider_filter}
    """

    providers = db.execute(text(providers_query), params).fetchall()

    # Build slot availability map
    # Key: start_time (HH:MM), Value: list of available provider IDs
    slot_map = {}

    for provider_row in providers:
        prov_id = str(provider_row[0])
        prov_name = provider_row[1]
        is_available = provider_row[4]
        sched_start = provider_row[2]
        sched_end = provider_row[3]

        if not is_available or not sched_start or not sched_end:
            continue

        # Convert to datetime for calculations
        if hasattr(sched_start, 'hour'):
            start_dt = datetime.combine(target_date, sched_start)
            end_dt = datetime.combine(target_date, sched_end)
        else:
            # String format
            start_dt = datetime.combine(target_date, datetime.strptime(str(sched_start)[:5], "%H:%M").time())
            end_dt = datetime.combine(target_date, datetime.strptime(str(sched_end)[:5], "%H:%M").time())

        # Get existing appointments for this provider on this date
        appointments = db.execute(
            text("""
                SELECT start_time, end_time FROM appointments
                WHERE provider_id = :provider_id
                    AND DATE(start_time) = :target_date
                    AND status != 'cancelled'
                ORDER BY start_time
            """),
            {"provider_id": prov_id, "target_date": target_date}
        ).fetchall()

        # Build list of booked time ranges
        booked_ranges = [
            (appt[0], appt[1] + timedelta(minutes=buffer_minutes))
            for appt in appointments
        ]

        # Generate slots
        current_slot = start_dt
        while current_slot + timedelta(minutes=slot_duration) <= end_dt:
            slot_end = current_slot + timedelta(minutes=slot_duration)

            # Skip if before minimum notice time
            if current_slot < min_bookable_time:
                current_slot += timedelta(minutes=slot_duration)
                continue

            # Check for conflicts with booked appointments
            is_free = True
            for booked_start, booked_end in booked_ranges:
                if current_slot < booked_end and slot_end > booked_start:
                    is_free = False
                    break

            if is_free:
                # Calculate max duration from this slot
                max_duration = slot_duration
                next_booking = end_dt
                for booked_start, _ in booked_ranges:
                    if booked_start > current_slot:
                        next_booking = min(next_booking, booked_start)
                        break
                max_duration = int((next_booking - current_slot).total_seconds() / 60)

                # If duration filter specified, check if it fits
                if duration and max_duration < duration:
                    current_slot += timedelta(minutes=slot_duration)
                    continue

                slot_key = current_slot.strftime("%H:%M")
                if slot_key not in slot_map:
                    slot_map[slot_key] = {
                        "start_time": current_slot.isoformat(),
                        "providers": [],
                        "max_duration_minutes": 0
                    }

                slot_map[slot_key]["providers"].append({
                    "id": prov_id,
                    "name": prov_name
                })
                slot_map[slot_key]["max_duration_minutes"] = max(
                    slot_map[slot_key]["max_duration_minutes"],
                    max_duration
                )

            current_slot += timedelta(minutes=slot_duration)

    # Convert to sorted list
    slots = []
    for slot_key in sorted(slot_map.keys()):
        slot_data = slot_map[slot_key]
        slot_start = datetime.fromisoformat(slot_data["start_time"])
        slot_end = slot_start + timedelta(minutes=slot_duration)

        slots.append({
            "start_time": slot_data["start_time"],
            "end_time": slot_end.isoformat(),
            "providers": slot_data["providers"],
            "available_count": len(slot_data["providers"]),
            "max_duration_minutes": slot_data["max_duration_minutes"]
        })

    return {
        "date": date,
        "slot_duration_minutes": slot_duration,
        "slots": slots
    }


# =============================================================================
# Helper Functions
# =============================================================================

async def _calculate_slots_for_date(
    db: Session,
    config_id: str,
    target_date: date,
    provider_id: Optional[str],
    duration: Optional[int],
    slot_duration: int
) -> int:
    """Calculate number of available slots for a date."""
    day_of_week = target_date.weekday()

    # Get providers with availability for this day
    provider_filter = ""
    params = {
        "config_id": config_id,
        "day_of_week": day_of_week,
        "target_date": target_date
    }

    if provider_id:
        provider_filter = "AND p.id = :provider_id"
        params["provider_id"] = provider_id

    query = f"""
        SELECT
            p.id,
            COALESCE(so.start_time, ps.start_time) as start_time,
            COALESCE(so.end_time, ps.end_time) as end_time,
            COALESCE(so.is_available, true) as is_available
        FROM providers p
        LEFT JOIN provider_schedules ps ON p.id = ps.provider_id
            AND ps.day_of_week = :day_of_week
            AND ps.is_active = true
        LEFT JOIN schedule_overrides so ON p.id = so.provider_id
            AND so.override_date = :target_date
        WHERE p.config_id = :config_id
            AND p.is_active = true
            {provider_filter}
    """

    providers = db.execute(text(query), params).fetchall()

    total_slots = 0
    for provider_row in providers:
        prov_id = str(provider_row[0])
        is_available = provider_row[3]
        sched_start = provider_row[1]
        sched_end = provider_row[2]

        if not is_available or not sched_start or not sched_end:
            continue

        # Convert to minutes
        if hasattr(sched_start, 'hour'):
            start_mins = sched_start.hour * 60 + sched_start.minute
            end_mins = sched_end.hour * 60 + sched_end.minute
        else:
            parts = str(sched_start)[:5].split(":")
            start_mins = int(parts[0]) * 60 + int(parts[1])
            parts = str(sched_end)[:5].split(":")
            end_mins = int(parts[0]) * 60 + int(parts[1])

        # Calculate potential slots
        potential_slots = (end_mins - start_mins) // slot_duration

        # Subtract booked appointments
        booked = db.execute(
            text("""
                SELECT COUNT(*) FROM appointments
                WHERE provider_id = :provider_id
                    AND DATE(start_time) = :target_date
                    AND status != 'cancelled'
            """),
            {"provider_id": prov_id, "target_date": target_date}
        ).scalar() or 0

        available = max(0, potential_slots - booked)
        total_slots += available

    return total_slots

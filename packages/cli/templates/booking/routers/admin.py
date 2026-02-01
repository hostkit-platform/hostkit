"""Admin endpoints for booking management.

Provides CRUD operations for:
- Providers
- Services
- Rooms
- Resources (Phase 3)
- Classes (Phase 3)
"""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from dependencies import get_config_id

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class ProviderCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    is_visible: bool = True


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    is_visible: Optional[bool] = None
    is_active: Optional[bool] = None


class ServiceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    duration_minutes: int
    price_cents: int
    category: Optional[str] = None
    requires_new_patient: bool = False
    min_notice_hours: Optional[int] = None
    max_advance_days: Optional[int] = None


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    price_cents: Optional[int] = None
    category: Optional[str] = None
    requires_new_patient: Optional[bool] = None
    min_notice_hours: Optional[int] = None
    max_advance_days: Optional[int] = None
    is_active: Optional[bool] = None


class ScheduleEntry(BaseModel):
    day_of_week: int  # 0=Monday, 6=Sunday
    start_time: str   # HH:MM
    end_time: str     # HH:MM


class ResourceCreate(BaseModel):
    name: str
    resource_type: str  # table, bay, room, desk
    description: Optional[str] = None
    capacity: int = 1
    attributes: Optional[dict] = None


class ClassCreate(BaseModel):
    name: str
    description: Optional[str] = None
    instructor_id: Optional[str] = None
    duration_minutes: int
    capacity: int
    price_cents: int
    category: Optional[str] = None


class ClassScheduleCreate(BaseModel):
    class_id: str
    start_time: str  # ISO format
    capacity_override: Optional[int] = None
    price_override_cents: Optional[int] = None
    instructor_override_id: Optional[str] = None


# =============================================================================
# Provider Management
# =============================================================================

@router.post("/providers")
async def create_provider(
    data: ProviderCreate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Create a new provider."""
    result = db.execute(
        text("""
            INSERT INTO providers (config_id, name, email, phone, bio, avatar_url, is_visible)
            VALUES (:config_id, :name, :email, :phone, :bio, :avatar_url, :is_visible)
            RETURNING id
        """),
        {
            "config_id": config_id,
            "name": data.name,
            "email": data.email,
            "phone": data.phone,
            "bio": data.bio,
            "avatar_url": data.avatar_url,
            "is_visible": data.is_visible
        }
    )
    provider_id = result.fetchone()[0]
    db.commit()

    return {"id": str(provider_id), "name": data.name}


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    data: ProviderUpdate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Update a provider."""
    updates = []
    params = {"provider_id": provider_id, "config_id": config_id}

    for field in ["name", "email", "phone", "bio", "avatar_url", "is_visible", "is_active"]:
        value = getattr(data, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    query = f"""
        UPDATE providers SET {', '.join(updates)}
        WHERE id = :provider_id AND config_id = :config_id
    """
    db.execute(text(query), params)
    db.commit()

    return {"updated": True, "id": provider_id}


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Soft-delete a provider (set is_active=false)."""
    db.execute(
        text("""
            UPDATE providers SET is_active = false
            WHERE id = :provider_id AND config_id = :config_id
        """),
        {"provider_id": provider_id, "config_id": config_id}
    )
    db.commit()

    return {"deleted": True, "id": provider_id}


@router.post("/providers/{provider_id}/schedule")
async def set_provider_schedule(
    provider_id: str,
    schedules: List[ScheduleEntry],
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Set provider's weekly schedule (replaces existing)."""
    # Delete existing schedules
    db.execute(
        text("DELETE FROM provider_schedules WHERE provider_id = :provider_id"),
        {"provider_id": provider_id}
    )

    # Insert new schedules
    for sched in schedules:
        db.execute(
            text("""
                INSERT INTO provider_schedules (provider_id, day_of_week, start_time, end_time)
                VALUES (:provider_id, :day_of_week, :start_time, :end_time)
            """),
            {
                "provider_id": provider_id,
                "day_of_week": sched.day_of_week,
                "start_time": sched.start_time,
                "end_time": sched.end_time
            }
        )

    db.commit()

    return {"updated": True, "provider_id": provider_id, "schedules": len(schedules)}


@router.post("/providers/{provider_id}/services")
async def link_provider_services(
    provider_id: str,
    service_ids: List[str],
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Link services to a provider."""
    # Delete existing links
    db.execute(
        text("DELETE FROM provider_services WHERE provider_id = :provider_id"),
        {"provider_id": provider_id}
    )

    # Insert new links
    for service_id in service_ids:
        db.execute(
            text("""
                INSERT INTO provider_services (provider_id, service_id)
                VALUES (:provider_id, :service_id)
                ON CONFLICT DO NOTHING
            """),
            {"provider_id": provider_id, "service_id": service_id}
        )

    db.commit()

    return {"updated": True, "provider_id": provider_id, "services": len(service_ids)}


# =============================================================================
# Service Management
# =============================================================================

@router.post("/services")
async def create_service(
    data: ServiceCreate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Create a new service."""
    result = db.execute(
        text("""
            INSERT INTO services (
                config_id, name, description, duration_minutes, price_cents,
                category, requires_new_patient, min_notice_hours, max_advance_days
            )
            VALUES (
                :config_id, :name, :description, :duration_minutes, :price_cents,
                :category, :requires_new_patient, :min_notice_hours, :max_advance_days
            )
            RETURNING id
        """),
        {
            "config_id": config_id,
            "name": data.name,
            "description": data.description,
            "duration_minutes": data.duration_minutes,
            "price_cents": data.price_cents,
            "category": data.category,
            "requires_new_patient": data.requires_new_patient,
            "min_notice_hours": data.min_notice_hours,
            "max_advance_days": data.max_advance_days
        }
    )
    service_id = result.fetchone()[0]
    db.commit()

    return {"id": str(service_id), "name": data.name}


@router.patch("/services/{service_id}")
async def update_service(
    service_id: str,
    data: ServiceUpdate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Update a service."""
    updates = []
    params = {"service_id": service_id, "config_id": config_id}

    for field in ["name", "description", "duration_minutes", "price_cents", "category",
                  "requires_new_patient", "min_notice_hours", "max_advance_days", "is_active"]:
        value = getattr(data, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    query = f"""
        UPDATE services SET {', '.join(updates)}
        WHERE id = :service_id AND config_id = :config_id
    """
    db.execute(text(query), params)
    db.commit()

    return {"updated": True, "id": service_id}


@router.delete("/services/{service_id}")
async def delete_service(
    service_id: str,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Soft-delete a service."""
    db.execute(
        text("""
            UPDATE services SET is_active = false
            WHERE id = :service_id AND config_id = :config_id
        """),
        {"service_id": service_id, "config_id": config_id}
    )
    db.commit()

    return {"deleted": True, "id": service_id}


# =============================================================================
# Phase 3: Resource Management
# =============================================================================

@router.get("/resources")
async def list_resources(
    resource_type: Optional[str] = None,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List all resources."""
    query = """
        SELECT id, name, resource_type, description, capacity, attributes, is_active
        FROM resources
        WHERE config_id = :config_id
    """
    params = {"config_id": config_id}

    if resource_type:
        query += " AND resource_type = :resource_type"
        params["resource_type"] = resource_type

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
                "attributes": row[5] or {},
                "is_active": row[6]
            }
            for row in results
        ]
    }


@router.post("/resources")
async def create_resource(
    data: ResourceCreate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Create a new resource (table, bay, room)."""
    import json

    result = db.execute(
        text("""
            INSERT INTO resources (config_id, name, resource_type, description, capacity, attributes)
            VALUES (:config_id, :name, :resource_type, :description, :capacity, :attributes)
            RETURNING id
        """),
        {
            "config_id": config_id,
            "name": data.name,
            "resource_type": data.resource_type,
            "description": data.description,
            "capacity": data.capacity,
            "attributes": json.dumps(data.attributes or {})
        }
    )
    resource_id = result.fetchone()[0]
    db.commit()

    return {"id": str(resource_id), "name": data.name}


@router.post("/resources/{resource_id}/schedule")
async def set_resource_schedule(
    resource_id: str,
    schedules: List[ScheduleEntry],
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Set resource's weekly schedule."""
    db.execute(
        text("DELETE FROM resource_schedules WHERE resource_id = :resource_id"),
        {"resource_id": resource_id}
    )

    for sched in schedules:
        db.execute(
            text("""
                INSERT INTO resource_schedules (resource_id, day_of_week, start_time, end_time)
                VALUES (:resource_id, :day_of_week, :start_time, :end_time)
            """),
            {
                "resource_id": resource_id,
                "day_of_week": sched.day_of_week,
                "start_time": sched.start_time,
                "end_time": sched.end_time
            }
        )

    db.commit()

    return {"updated": True, "resource_id": resource_id}


# =============================================================================
# Phase 3: Class Management
# =============================================================================

@router.get("/classes")
async def list_classes(
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List all class types."""
    query = """
        SELECT c.id, c.name, c.description, c.duration_minutes, c.capacity,
               c.price_cents, c.category, c.is_active,
               p.id as instructor_id, p.name as instructor_name
        FROM classes c
        LEFT JOIN providers p ON c.instructor_id = p.id
        WHERE c.config_id = :config_id
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
                "is_active": row[7],
                "instructor": {
                    "id": str(row[8]),
                    "name": row[9]
                } if row[8] else None
            }
            for row in results
        ]
    }


@router.post("/classes")
async def create_class(
    data: ClassCreate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Create a new class type."""
    result = db.execute(
        text("""
            INSERT INTO classes (
                config_id, name, description, instructor_id,
                duration_minutes, capacity, price_cents, category
            )
            VALUES (
                :config_id, :name, :description, :instructor_id,
                :duration_minutes, :capacity, :price_cents, :category
            )
            RETURNING id
        """),
        {
            "config_id": config_id,
            "name": data.name,
            "description": data.description,
            "instructor_id": data.instructor_id,
            "duration_minutes": data.duration_minutes,
            "capacity": data.capacity,
            "price_cents": data.price_cents,
            "category": data.category
        }
    )
    class_id = result.fetchone()[0]
    db.commit()

    return {"id": str(class_id), "name": data.name}


@router.post("/classes/schedule")
async def schedule_class(
    data: ClassScheduleCreate,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """Schedule a class instance."""
    from datetime import datetime, timedelta

    # Get class info
    class_info = db.execute(
        text("SELECT duration_minutes, capacity FROM classes WHERE id = :class_id"),
        {"class_id": data.class_id}
    ).fetchone()

    if not class_info:
        raise HTTPException(status_code=404, detail="Class not found")

    start_time = datetime.fromisoformat(data.start_time.replace("Z", "+00:00"))
    end_time = start_time + timedelta(minutes=class_info[0])
    capacity = data.capacity_override or class_info[1]

    result = db.execute(
        text("""
            INSERT INTO class_schedules (
                class_id, start_time, end_time,
                capacity_override, price_override_cents, instructor_override_id,
                spots_remaining
            )
            VALUES (
                :class_id, :start_time, :end_time,
                :capacity_override, :price_override_cents, :instructor_override_id,
                :spots_remaining
            )
            RETURNING id
        """),
        {
            "class_id": data.class_id,
            "start_time": start_time,
            "end_time": end_time,
            "capacity_override": data.capacity_override,
            "price_override_cents": data.price_override_cents,
            "instructor_override_id": data.instructor_override_id,
            "spots_remaining": capacity
        }
    )
    schedule_id = result.fetchone()[0]
    db.commit()

    return {
        "id": str(schedule_id),
        "class_id": data.class_id,
        "start_time": start_time.isoformat(),
        "spots_remaining": capacity
    }


@router.get("/classes/schedule")
async def list_class_schedule(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    class_id: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    config_id: str = Depends(get_config_id)
):
    """List scheduled class instances."""
    query = """
        SELECT cs.id, cs.start_time, cs.end_time, cs.spots_remaining, cs.status,
               c.id as class_id, c.name as class_name, c.category,
               COALESCE(cs.capacity_override, c.capacity) as capacity,
               COALESCE(cs.price_override_cents, c.price_cents) as price_cents,
               COALESCE(p2.name, p.name) as instructor_name
        FROM class_schedules cs
        JOIN classes c ON cs.class_id = c.id
        LEFT JOIN providers p ON c.instructor_id = p.id
        LEFT JOIN providers p2 ON cs.instructor_override_id = p2.id
        WHERE c.config_id = :config_id
    """
    params = {"config_id": config_id}

    if from_date:
        query += " AND cs.start_time >= :from_date"
        params["from_date"] = from_date

    if to_date:
        query += " AND cs.start_time < :to_date"
        params["to_date"] = to_date + " 23:59:59"

    if class_id:
        query += " AND cs.class_id = :class_id"
        params["class_id"] = class_id

    if status:
        query += " AND cs.status = :status"
        params["status"] = status

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
                "class_id": str(row[5]),
                "class_name": row[6],
                "category": row[7],
                "capacity": row[8],
                "price_cents": row[9],
                "instructor_name": row[10]
            }
            for row in results
        ]
    }

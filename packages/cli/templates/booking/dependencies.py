"""FastAPI dependencies for booking service."""

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db


def get_config_id(db: Session = Depends(get_db)) -> str:
    """Get the booking config ID for this project."""
    from config import settings

    result = db.execute(
        text("SELECT id FROM booking_configs WHERE project_id = :project_id"),
        {"project_id": settings.project_name}
    ).fetchone()

    if not result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Booking configuration not found"
        )

    return result[0]

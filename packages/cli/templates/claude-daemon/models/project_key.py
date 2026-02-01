"""Project API key model."""

from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ProjectKey(Base):
    """API keys for project access to Claude daemon.

    Each project gets one API key in the format: ck_{project}_{random}
    Keys are stored hashed for security.
    """
    __tablename__ = "project_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    api_key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)  # ck_{project}_xxx (first 8 chars of random)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Rate limiting
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=60)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=1_000_000)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<ProjectKey(project={self.project_name}, enabled={self.enabled})>"

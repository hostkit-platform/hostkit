"""User model for authentication."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    """User account model.

    Supports multiple authentication methods:
    - Email/password
    - OAuth (Google, Apple)
    - Magic links
    - Anonymous sessions
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    password_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_anonymous: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_sign_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    sessions: Mapped[list["Session"]] = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(
        "OAuthAccount",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User {self.id} email={self.email}>"

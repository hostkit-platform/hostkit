"""Session model for refresh token tracking."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Session(Base):
    """User session model.

    Tracks refresh tokens and session metadata for:
    - Token refresh/revocation
    - Session management (list, revoke)
    - Security tracking (IP, user agent)
    """

    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="sessions",
    )

    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_revoked(self) -> bool:
        """Check if session has been revoked."""
        return self.revoked_at is not None

    @property
    def is_valid(self) -> bool:
        """Check if session is valid (not expired and not revoked)."""
        return not self.is_expired and not self.is_revoked

    def __repr__(self) -> str:
        return f"<Session {self.id} user_id={self.user_id}>"

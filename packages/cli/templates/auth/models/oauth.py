"""OAuth account model for linked OAuth providers."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class OAuthAccount(Base):
    """OAuth account linked to a user.

    Stores OAuth provider information and tokens for:
    - Google Sign-In
    - Apple Sign-In
    - Other OAuth providers

    A user can have multiple OAuth accounts (one per provider).
    """

    __tablename__ = "oauth_accounts"

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
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    access_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    refresh_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="oauth_accounts",
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_provider_user",
        ),
    )

    def __repr__(self) -> str:
        return f"<OAuthAccount {self.provider}:{self.provider_user_id}>"

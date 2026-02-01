"""Magic link and email verification models."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class MagicLink(Base):
    """Magic link for passwordless authentication.

    Stores hashed tokens for one-time use magic link authentication.
    """

    __tablename__ = "magic_links"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
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
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def is_expired(self) -> bool:
        """Check if magic link has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_used(self) -> bool:
        """Check if magic link has been used."""
        return self.used_at is not None

    def __repr__(self) -> str:
        return f"<MagicLink {self.id} email={self.email}>"


class EmailVerification(Base):
    """Email verification token.

    Used to verify user email addresses after signup.
    """

    __tablename__ = "email_verifications"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
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
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def is_expired(self) -> bool:
        """Check if verification has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_verified(self) -> bool:
        """Check if email has been verified."""
        return self.verified_at is not None

    def __repr__(self) -> str:
        return f"<EmailVerification {self.id} email={self.email}>"


class PasswordReset(Base):
    """Password reset token.

    Used for password recovery flow.
    """

    __tablename__ = "password_resets"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
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
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    @property
    def is_expired(self) -> bool:
        """Check if reset token has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_used(self) -> bool:
        """Check if reset token has been used."""
        return self.used_at is not None

    def __repr__(self) -> str:
        return f"<PasswordReset {self.id} email={self.email}>"

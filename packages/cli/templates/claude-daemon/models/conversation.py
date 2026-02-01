"""Conversation model."""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from models.base import Base


class Conversation(Base):
    """A conversation thread between a project and Claude.

    Conversations persist messages and can be resumed.
    """
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    project_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("project_keys.project_name", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Conversation metadata
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Statistics
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at"
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, project={self.project_name}, messages={self.message_count})>"

"""Message model."""

import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from models.base import Base


class Message(Base):
    """A message within a conversation.

    Supports user messages, assistant responses, and tool results.
    """
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Message content
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user, assistant, tool_result
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Tool-related fields
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # If assistant with tool use
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # If tool_result
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # If tool_result

    # Token tracking
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, role={self.role}, tokens={self.input_tokens + self.output_tokens})>"

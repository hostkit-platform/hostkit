"""Tool execution audit log model."""

import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from models.base import Base


class ToolExecution(Base):
    """Audit log for tool executions.

    Every tool call is logged with full context for security and debugging.
    """
    __tablename__ = "tool_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("project_keys.project_name", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Tool details
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tool_input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tool_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Execution status
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<ToolExecution(id={self.id}, tool={self.tool_name}, success={self.success})>"

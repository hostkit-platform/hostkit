"""Usage tracking model."""

from datetime import date, datetime
from sqlalchemy import String, Integer, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class UsageTracking(Base):
    """Daily usage statistics per project.

    Tracks requests, tokens, and tool calls for billing/quota purposes.
    """
    __tablename__ = "usage_tracking"
    __table_args__ = (
        UniqueConstraint("project_name", "date", name="uq_project_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_name: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("project_keys.project_name", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Usage counters
    requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<UsageTracking(project={self.project_name}, date={self.date}, tokens={self.input_tokens + self.output_tokens})>"

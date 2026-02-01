"""Quota and rate limiting manager.

Handles per-project rate limiting and daily token quotas.
"""

from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.project_key import ProjectKey
from models.usage import UsageTracking


class QuotaManager:
    """Manages rate limiting and usage quotas for projects."""

    def __init__(self, db: AsyncSession):
        """Initialize quota manager with database session."""
        self.db = db
        self._request_timestamps: dict[str, list[datetime]] = {}

    async def check_rate_limit(self, project: ProjectKey) -> tuple[bool, str | None]:
        """Check if project is within rate limit.

        Args:
            project: Project to check

        Returns:
            Tuple of (allowed, error_message)
        """
        now = datetime.utcnow()
        project_name = project.project_name

        # Get request timestamps for this project
        if project_name not in self._request_timestamps:
            self._request_timestamps[project_name] = []

        timestamps = self._request_timestamps[project_name]

        # Remove timestamps older than 1 minute
        one_minute_ago = now.timestamp() - 60
        timestamps = [t for t in timestamps if t.timestamp() > one_minute_ago]
        self._request_timestamps[project_name] = timestamps

        # Check if under limit
        if len(timestamps) >= project.rate_limit_rpm:
            return False, f"Rate limit exceeded ({project.rate_limit_rpm} requests/minute)"

        # Add current timestamp
        timestamps.append(now)
        return True, None

    async def check_daily_quota(self, project: ProjectKey) -> tuple[bool, int]:
        """Check if project has remaining daily token quota.

        Args:
            project: Project to check

        Returns:
            Tuple of (has_quota, remaining_tokens)
        """
        today = date.today()

        result = await self.db.execute(
            select(UsageTracking)
            .where(
                UsageTracking.project_name == project.project_name,
                UsageTracking.date == today,
            )
        )
        usage = result.scalar_one_or_none()

        if not usage:
            return True, project.daily_token_limit

        total_used = usage.input_tokens + usage.output_tokens
        remaining = project.daily_token_limit - total_used

        return remaining > 0, max(0, remaining)

    async def record_usage(
        self,
        project_name: str,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int = 0,
    ) -> None:
        """Record usage for a project.

        Args:
            project_name: Name of the project
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens used
            tool_calls: Number of tool calls made
        """
        today = date.today()

        result = await self.db.execute(
            select(UsageTracking)
            .where(
                UsageTracking.project_name == project_name,
                UsageTracking.date == today,
            )
        )
        usage = result.scalar_one_or_none()

        if usage:
            usage.requests += 1
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.tool_calls += tool_calls
        else:
            usage = UsageTracking(
                project_name=project_name,
                date=today,
                requests=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
            )
            self.db.add(usage)

"""Rate limiting service for HostKit deployments."""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from hostkit.database import get_db


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    project_name: str
    max_deploys: int
    window_minutes: int
    failure_cooldown_minutes: int
    consecutive_failure_limit: int
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RateLimitConfig":
        """Create from database row."""
        return cls(
            project_name=data["project_name"],
            max_deploys=data["max_deploys"],
            window_minutes=data["window_minutes"],
            failure_cooldown_minutes=data["failure_cooldown_minutes"],
            consecutive_failure_limit=data["consecutive_failure_limit"],
            updated_at=data["updated_at"],
        )

    @classmethod
    def default(cls, project_name: str) -> "RateLimitConfig":
        """Create default configuration."""
        return cls(
            project_name=project_name,
            max_deploys=10,
            window_minutes=60,
            failure_cooldown_minutes=5,
            consecutive_failure_limit=3,
            updated_at=datetime.utcnow().isoformat(),
        )


@dataclass
class RateLimitStatus:
    """Current rate limit status for a project."""

    project_name: str
    config: RateLimitConfig
    deploys_in_window: int
    consecutive_failures: int
    in_cooldown: bool
    cooldown_ends_at: str | None
    is_blocked: bool
    block_reason: str | None


class RateLimitError(Exception):
    """Error raised when rate limit is exceeded."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class RateLimitService:
    """Service for managing deployment rate limits."""

    def __init__(self) -> None:
        self.db = get_db()

    def get_config(self, project_name: str) -> RateLimitConfig:
        """Get rate limit configuration for a project.

        Returns default config if none exists.
        """
        config = self.db.get_rate_limit_config(project_name)
        if config:
            return RateLimitConfig.from_dict(config)
        return RateLimitConfig.default(project_name)

    def set_config(
        self,
        project_name: str,
        max_deploys: int | None = None,
        window_minutes: int | None = None,
        failure_cooldown_minutes: int | None = None,
        consecutive_failure_limit: int | None = None,
    ) -> RateLimitConfig:
        """Set or update rate limit configuration.

        Creates a new config if one doesn't exist.
        """
        existing = self.db.get_rate_limit_config(project_name)

        if existing:
            updated = self.db.update_rate_limit_config(
                project_name,
                max_deploys=max_deploys,
                window_minutes=window_minutes,
                failure_cooldown_minutes=failure_cooldown_minutes,
                consecutive_failure_limit=consecutive_failure_limit,
            )
        else:
            # Create with defaults, then override with provided values
            defaults = RateLimitConfig.default(project_name)
            updated = self.db.create_rate_limit_config(
                project_name,
                max_deploys=max_deploys if max_deploys is not None else defaults.max_deploys,
                window_minutes=window_minutes
                if window_minutes is not None
                else defaults.window_minutes,
                failure_cooldown_minutes=(
                    failure_cooldown_minutes
                    if failure_cooldown_minutes is not None
                    else defaults.failure_cooldown_minutes
                ),
                consecutive_failure_limit=(
                    consecutive_failure_limit
                    if consecutive_failure_limit is not None
                    else defaults.consecutive_failure_limit
                ),
            )

        return (
            RateLimitConfig.from_dict(updated) if updated else RateLimitConfig.default(project_name)
        )

    def reset_config(self, project_name: str) -> bool:
        """Reset rate limit configuration to defaults (deletes custom config)."""
        return self.db.delete_rate_limit_config(project_name)

    def get_status(self, project_name: str) -> RateLimitStatus:
        """Get the current rate limit status for a project."""
        config = self.get_config(project_name)

        # Calculate window start time
        window_start = datetime.utcnow() - timedelta(minutes=config.window_minutes)
        since = window_start.isoformat()

        # Count deploys in window
        deploys_in_window = self.db.count_deploys_since(project_name, since)

        # Check consecutive failures
        consecutive_failures = self.db.get_consecutive_failures(project_name)

        # Check cooldown
        in_cooldown = False
        cooldown_ends_at = None
        if consecutive_failures >= config.consecutive_failure_limit:
            last_failure = self.db.get_last_failed_deploy(project_name)
            if last_failure:
                failure_time = datetime.fromisoformat(last_failure["deployed_at"])
                cooldown_end = failure_time + timedelta(minutes=config.failure_cooldown_minutes)
                if datetime.utcnow() < cooldown_end:
                    in_cooldown = True
                    cooldown_ends_at = cooldown_end.isoformat()

        # Determine if blocked
        is_blocked = False
        block_reason = None

        # max_deploys <= 0 means unlimited (rate limiting disabled)
        if config.max_deploys > 0 and deploys_in_window >= config.max_deploys:
            is_blocked = True
            block_reason = (
                f"Rate limit exceeded: {deploys_in_window}/{config.max_deploys} deploys "
                f"in the last {config.window_minutes} minutes"
            )
        elif in_cooldown:
            is_blocked = True
            block_reason = (
                f"Cooldown active after {consecutive_failures} consecutive failures. "
                f"Wait until {cooldown_ends_at} or use --override-ratelimit"
            )

        return RateLimitStatus(
            project_name=project_name,
            config=config,
            deploys_in_window=deploys_in_window,
            consecutive_failures=consecutive_failures,
            in_cooldown=in_cooldown,
            cooldown_ends_at=cooldown_ends_at,
            is_blocked=is_blocked,
            block_reason=block_reason,
        )

    def check_rate_limit(self, project_name: str) -> bool:
        """Check if a deploy is allowed.

        Returns True if allowed.
        Raises RateLimitError if blocked.
        """
        status = self.get_status(project_name)

        if status.is_blocked:
            raise RateLimitError(
                code="RATE_LIMIT_EXCEEDED",
                message=status.block_reason or "Rate limit exceeded",
                suggestion="Use --override-ratelimit to bypass the rate limit",
            )

        return True

    def record_deploy(
        self,
        project_name: str,
        success: bool = True,
        duration_ms: int | None = None,
        source_type: str | None = None,
        files_synced: int | None = None,
        override_used: bool = False,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Record a deploy attempt.

        Should be called after every deploy (success or failure).
        """
        deployed_by = os.environ.get("SUDO_USER") or os.environ.get("USER", "unknown")

        return self.db.record_deploy(
            project_name=project_name,
            deployed_by=deployed_by,
            success=success,
            duration_ms=duration_ms,
            source_type=source_type,
            files_synced=files_synced,
            override_used=override_used,
            error_message=error_message,
        )

    def get_deploy_history(
        self,
        project_name: str,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get deploy history for a project.

        Args:
            project_name: Project name
            since: ISO timestamp or duration string (e.g., "1h", "24h", "7d")
            limit: Maximum number of results
        """
        # Convert duration string to ISO timestamp if needed
        since_iso = None
        if since:
            since_iso = self._parse_since(since)

        return self.db.list_deploys(project_name, since=since_iso, limit=limit)

    def clear_history(self, project_name: str) -> int:
        """Clear deploy history for a project.

        Returns count of deleted records.
        """
        return self.db.delete_deploy_history_for_project(project_name)

    def _parse_since(self, since: str) -> str:
        """Parse a duration string into an ISO timestamp.

        Supports formats like: 1h, 24h, 7d, 30m
        Also accepts ISO timestamps directly.
        """
        # Check if it's already an ISO timestamp
        try:
            datetime.fromisoformat(since)
            return since
        except ValueError:
            pass

        # Parse duration string
        match = re.match(r"^(\d+)([hdm])$", since.lower())
        if not match:
            # Default to treating as hours
            try:
                hours = int(since)
                dt = datetime.utcnow() - timedelta(hours=hours)
                return dt.isoformat()
            except ValueError:
                raise ValueError(
                    f"Invalid duration format: {since}. Use formats like 1h, 24h, 7d, 30m"
                )

        value = int(match.group(1))
        unit = match.group(2)

        if unit == "h":
            delta = timedelta(hours=value)
        elif unit == "d":
            delta = timedelta(days=value)
        elif unit == "m":
            delta = timedelta(minutes=value)
        else:
            raise ValueError(f"Unknown duration unit: {unit}")

        dt = datetime.utcnow() - delta
        return dt.isoformat()

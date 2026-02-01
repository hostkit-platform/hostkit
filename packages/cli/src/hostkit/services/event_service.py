"""Event service for structured HostKit operation logging."""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

from hostkit.database import get_db


# Event categories
class EventCategory:
    """Valid event categories."""
    DEPLOY = "deploy"
    HEALTH = "health"
    AUTH = "auth"
    MIGRATE = "migrate"
    CRON = "cron"
    WORKER = "worker"
    SERVICE = "service"
    CHECKPOINT = "checkpoint"
    ALERT = "alert"
    SANDBOX = "sandbox"
    ENVIRONMENT = "environment"
    PROJECT = "project"
    GIT = "git"


# Event types per category
class EventType:
    """Valid event types."""
    # General
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"

    # Deploy-specific
    RATE_LIMITED = "rate_limited"
    PAUSED = "paused"
    RESUMED = "resumed"

    # Service-specific
    STOPPED = "stopped"
    RESTARTED = "restarted"

    # Config-specific
    ENABLED = "enabled"
    DISABLED = "disabled"
    CONFIGURED = "configured"

    # Health-specific
    PASSED = "passed"

    # Sandbox-specific
    PROMOTED = "promoted"
    EXTENDED = "extended"

    # General CRUD
    CREATED = "created"
    DELETED = "deleted"
    UPDATED = "updated"


# Log levels
class EventLevel:
    """Valid event levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class Event:
    """A structured event."""
    id: int
    project_name: str
    category: str
    event_type: str
    level: str
    message: str
    data: dict[str, Any] | None
    created_at: str
    created_by: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        """Create an Event from a database row dict."""
        data = d.get("data")
        if data and isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None
        return cls(
            id=d["id"],
            project_name=d["project_name"],
            category=d["category"],
            event_type=d["event_type"],
            level=d["level"],
            message=d["message"],
            data=data,
            created_at=d["created_at"],
            created_by=d.get("created_by"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class EventServiceError(Exception):
    """Base exception for event service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class EventService:
    """Service for emitting and querying structured events."""

    def __init__(self) -> None:
        self.db = get_db()

    def _get_current_user(self) -> str | None:
        """Get the current user from environment or sudo context."""
        # Check for SUDO_USER first (if running under sudo)
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            return sudo_user
        # Fall back to USER
        return os.environ.get("USER")

    def _validate_project(self, project_name: str) -> None:
        """Validate that a project exists."""
        proj = self.db.get_project(project_name)
        if not proj:
            raise EventServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def emit(
        self,
        project_name: str,
        category: str,
        event_type: str,
        message: str,
        level: str = EventLevel.INFO,
        data: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> int:
        """Emit an event and return its ID.

        Args:
            project_name: Name of the project
            category: Event category (deploy, health, auth, etc.)
            event_type: Event type (started, completed, failed, etc.)
            message: Human-readable message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            data: Optional structured data
            created_by: Username who triggered the event (auto-detected if not provided)

        Returns:
            The event ID
        """
        self._validate_project(project_name)

        if created_by is None:
            created_by = self._get_current_user()

        # Serialize data to JSON string
        data_json = json.dumps(data) if data else None

        return self.db.create_event(
            project_name=project_name,
            category=category,
            event_type=event_type,
            message=message,
            level=level,
            data=data_json,
            created_by=created_by,
        )

    def get(self, event_id: int) -> Event | None:
        """Get an event by ID."""
        row = self.db.get_event(event_id)
        return Event.from_dict(row) if row else None

    def query(
        self,
        project_name: str,
        category: str | None = None,
        level: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Query events with filters.

        Args:
            project_name: Name of the project
            category: Filter by category (comma-separated for multiple)
            level: Minimum log level
            since: Start time (ISO format or relative like "1h", "24h")
            until: End time (ISO format or relative)
            limit: Maximum events to return
            offset: Skip first N events

        Returns:
            List of events matching the filters
        """
        self._validate_project(project_name)

        # Parse relative time strings
        since_iso = self._parse_time(since) if since else None
        until_iso = self._parse_time(until) if until else None

        rows = self.db.list_events(
            project_name=project_name,
            category=category,
            level=level,
            since=since_iso,
            until=until_iso,
            limit=limit,
            offset=offset,
        )
        return [Event.from_dict(row) for row in rows]

    def count(
        self,
        project_name: str,
        category: str | None = None,
        level: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        """Count events matching filters."""
        self._validate_project(project_name)

        since_iso = self._parse_time(since) if since else None
        until_iso = self._parse_time(until) if until else None

        return self.db.count_events(
            project_name=project_name,
            category=category,
            level=level,
            since=since_iso,
            until=until_iso,
        )

    def cleanup(self, older_than_days: int = 30) -> int:
        """Delete old events. Returns count deleted."""
        return self.db.delete_old_events(older_than_days)

    def _parse_time(self, time_str: str) -> str:
        """Parse a time string into ISO format.

        Supports:
        - Relative: "1h", "30m", "24h", "7d"
        - ISO format: "2025-12-15", "2025-12-15T10:00:00"
        - Human readable: "1 hour ago", "2 days ago"
        """
        import re

        time_str = time_str.strip().lower()

        # If already ISO format, return as-is
        if re.match(r"^\d{4}-\d{2}-\d{2}", time_str):
            return time_str

        now = datetime.utcnow()

        # Relative format: 1h, 30m, 24h, 7d
        match = re.match(r"^(\d+)\s*(h|hour|hours|m|min|mins|minutes|d|day|days|w|week|weeks)$", time_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("h"):
                result = now - timedelta(hours=value)
            elif unit.startswith("m"):
                result = now - timedelta(minutes=value)
            elif unit.startswith("d"):
                result = now - timedelta(days=value)
            elif unit.startswith("w"):
                result = now - timedelta(weeks=value)
            else:
                result = now
            return result.isoformat()

        # Human readable: "X hours/days ago"
        match = re.match(r"^(\d+)\s*(hour|hours|day|days|minute|minutes|week|weeks)\s+ago$", time_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            if "hour" in unit:
                result = now - timedelta(hours=value)
            elif "day" in unit:
                result = now - timedelta(days=value)
            elif "minute" in unit:
                result = now - timedelta(minutes=value)
            elif "week" in unit:
                result = now - timedelta(weeks=value)
            else:
                result = now
            return result.isoformat()

        # If we can't parse, return as-is and let the database handle it
        return time_str

    # Convenience methods for common events

    def deploy_started(
        self,
        project_name: str,
        source: str | None = None,
        git_url: str | None = None,
        git_branch: str | None = None,
    ) -> int:
        """Emit a deploy started event."""
        data = {}
        if source:
            data["source"] = source
        if git_url:
            data["git_url"] = git_url
        if git_branch:
            data["git_branch"] = git_branch

        return self.emit(
            project_name=project_name,
            category=EventCategory.DEPLOY,
            event_type=EventType.STARTED,
            message=f"Deploy started for {project_name}",
            level=EventLevel.INFO,
            data=data if data else None,
        )

    def deploy_completed(
        self,
        project_name: str,
        files_synced: int = 0,
        duration_seconds: float = 0,
        release_name: str | None = None,
    ) -> int:
        """Emit a deploy completed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.DEPLOY,
            event_type=EventType.COMPLETED,
            message=f"Deploy completed for {project_name} ({files_synced} files, {duration_seconds:.1f}s)",
            level=EventLevel.INFO,
            data={
                "files_synced": files_synced,
                "duration_seconds": round(duration_seconds, 2),
                "release_name": release_name,
            },
        )

    def deploy_failed(
        self,
        project_name: str,
        error: str,
        duration_seconds: float = 0,
    ) -> int:
        """Emit a deploy failed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.DEPLOY,
            event_type=EventType.FAILED,
            message=f"Deploy failed for {project_name}: {error}",
            level=EventLevel.ERROR,
            data={
                "error": error,
                "duration_seconds": round(duration_seconds, 2),
            },
        )

    def deploy_rate_limited(
        self,
        project_name: str,
        deploys_in_window: int,
        window_minutes: int,
    ) -> int:
        """Emit a deploy rate limited event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.DEPLOY,
            event_type=EventType.RATE_LIMITED,
            message=f"Deploy rate limited for {project_name}: {deploys_in_window}/{window_minutes}min",
            level=EventLevel.WARNING,
            data={
                "deploys_in_window": deploys_in_window,
                "window_minutes": window_minutes,
            },
        )

    def health_check_passed(self, project_name: str, endpoint: str, response_time_ms: int) -> int:
        """Emit a health check passed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.HEALTH,
            event_type=EventType.PASSED,
            message=f"Health check passed for {project_name} ({response_time_ms}ms)",
            level=EventLevel.DEBUG,
            data={
                "endpoint": endpoint,
                "response_time_ms": response_time_ms,
            },
        )

    def health_check_failed(self, project_name: str, endpoint: str, error: str) -> int:
        """Emit a health check failed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.HEALTH,
            event_type=EventType.FAILED,
            message=f"Health check failed for {project_name}: {error}",
            level=EventLevel.ERROR,
            data={
                "endpoint": endpoint,
                "error": error,
            },
        )

    def service_started(self, project_name: str, service_name: str | None = None) -> int:
        """Emit a service started event."""
        service = service_name or f"hostkit-{project_name}"
        return self.emit(
            project_name=project_name,
            category=EventCategory.SERVICE,
            event_type=EventType.STARTED,
            message=f"Service {service} started",
            level=EventLevel.INFO,
            data={"service_name": service},
        )

    def service_stopped(self, project_name: str, service_name: str | None = None) -> int:
        """Emit a service stopped event."""
        service = service_name or f"hostkit-{project_name}"
        return self.emit(
            project_name=project_name,
            category=EventCategory.SERVICE,
            event_type=EventType.STOPPED,
            message=f"Service {service} stopped",
            level=EventLevel.INFO,
            data={"service_name": service},
        )

    def migration_started(self, project_name: str, framework: str) -> int:
        """Emit a migration started event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.MIGRATE,
            event_type=EventType.STARTED,
            message=f"Migration started for {project_name} ({framework})",
            level=EventLevel.INFO,
            data={"framework": framework},
        )

    def migration_completed(self, project_name: str, framework: str) -> int:
        """Emit a migration completed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.MIGRATE,
            event_type=EventType.COMPLETED,
            message=f"Migration completed for {project_name} ({framework})",
            level=EventLevel.INFO,
            data={"framework": framework},
        )

    def migration_failed(self, project_name: str, framework: str, error: str) -> int:
        """Emit a migration failed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.MIGRATE,
            event_type=EventType.FAILED,
            message=f"Migration failed for {project_name}: {error}",
            level=EventLevel.ERROR,
            data={"framework": framework, "error": error},
        )

    def project_paused(self, project_name: str, reason: str) -> int:
        """Emit a project paused event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.PROJECT,
            event_type=EventType.PAUSED,
            message=f"Project {project_name} auto-paused: {reason}",
            level=EventLevel.WARNING,
            data={"reason": reason},
        )

    def project_resumed(self, project_name: str) -> int:
        """Emit a project resumed event."""
        return self.emit(
            project_name=project_name,
            category=EventCategory.PROJECT,
            event_type=EventType.RESUMED,
            message=f"Project {project_name} resumed",
            level=EventLevel.INFO,
        )

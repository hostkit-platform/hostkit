"""Auto-pause service for HostKit.

Automatically pauses projects after repeated failures to prevent resource waste
and AI agent thrashing.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from hostkit.database import get_db
from hostkit.services.alert_service import send_alert


@dataclass
class AutoPauseConfig:
    """Configuration for auto-pause."""

    project_name: str
    enabled: bool
    failure_threshold: int
    window_minutes: int
    paused: bool
    paused_at: str | None
    paused_reason: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoPauseConfig":
        """Create from database row."""
        return cls(
            project_name=data["project_name"],
            enabled=bool(data.get("enabled", 0)),
            failure_threshold=data.get("failure_threshold", 5),
            window_minutes=data.get("window_minutes", 10),
            paused=bool(data.get("paused", 0)),
            paused_at=data.get("paused_at"),
            paused_reason=data.get("paused_reason"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    @classmethod
    def default(cls, project_name: str) -> "AutoPauseConfig":
        """Create default configuration (disabled)."""
        now = datetime.utcnow().isoformat()
        return cls(
            project_name=project_name,
            enabled=False,
            failure_threshold=5,
            window_minutes=10,
            paused=False,
            paused_at=None,
            paused_reason=None,
            created_at=now,
            updated_at=now,
        )


class AutoPauseError(Exception):
    """Exception for auto-pause errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class AutoPauseService:
    """Service for managing auto-pause on failures."""

    def __init__(self) -> None:
        self.db = get_db()

    def _validate_project(self, project_name: str) -> dict[str, Any]:
        """Validate that the project exists."""
        proj = self.db.get_project(project_name)
        if not proj:
            raise AutoPauseError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return proj

    def get_config(self, project_name: str) -> AutoPauseConfig:
        """Get auto-pause configuration for a project.

        Returns default config if none exists.
        """
        config = self.db.get_auto_pause_config(project_name)
        if config:
            return AutoPauseConfig.from_dict(config)
        return AutoPauseConfig.default(project_name)

    def set_config(
        self,
        project_name: str,
        enabled: bool | None = None,
        failure_threshold: int | None = None,
        window_minutes: int | None = None,
    ) -> AutoPauseConfig:
        """Set or update auto-pause configuration.

        Creates a new config if one doesn't exist.
        """
        self._validate_project(project_name)
        existing = self.db.get_auto_pause_config(project_name)

        if existing:
            updated = self.db.update_auto_pause_config(
                project_name,
                enabled=enabled,
                failure_threshold=failure_threshold,
                window_minutes=window_minutes,
            )
        else:
            # Create with defaults, then override with provided values
            defaults = AutoPauseConfig.default(project_name)
            updated = self.db.create_auto_pause_config(
                project_name,
                enabled=enabled if enabled is not None else defaults.enabled,
                failure_threshold=(
                    failure_threshold
                    if failure_threshold is not None
                    else defaults.failure_threshold
                ),
                window_minutes=(
                    window_minutes
                    if window_minutes is not None
                    else defaults.window_minutes
                ),
            )

        return AutoPauseConfig.from_dict(updated) if updated else AutoPauseConfig.default(project_name)

    def is_paused(self, project_name: str) -> bool:
        """Check if a project is currently paused."""
        return self.db.is_project_paused(project_name)

    def get_pause_info(self, project_name: str) -> dict[str, Any] | None:
        """Get pause information for a paused project.

        Returns None if not paused.
        """
        config = self.db.get_auto_pause_config(project_name)
        if not config or not config.get("paused"):
            return None

        return {
            "paused": True,
            "paused_at": config.get("paused_at"),
            "paused_reason": config.get("paused_reason"),
        }

    def pause(self, project_name: str, reason: str) -> dict[str, Any]:
        """Pause a project.

        Args:
            project_name: Project name
            reason: Reason for pausing (e.g., "5 failures in 10 minutes")

        Returns:
            Dict with pause details
        """
        self._validate_project(project_name)

        # Ensure config exists
        existing = self.db.get_auto_pause_config(project_name)
        if not existing:
            self.db.create_auto_pause_config(project_name)

        # Set paused state
        now = datetime.utcnow().isoformat()
        self.db.update_auto_pause_config(
            project_name,
            paused=True,
            paused_at=now,
            paused_reason=reason,
        )

        # Send alert
        try:
            send_alert(
                project_name=project_name,
                event_type="pause",
                event_status="failure",
                data={
                    "reason": reason,
                    "paused_at": now,
                    "action": "Run 'hostkit resume' to continue",
                },
            )
        except Exception:
            pass  # Don't fail pause if alert fails

        return {
            "project": project_name,
            "paused": True,
            "paused_at": now,
            "reason": reason,
        }

    def resume(self, project_name: str, reset_failures: bool = False) -> dict[str, Any]:
        """Resume a paused project.

        Args:
            project_name: Project name
            reset_failures: If True, clear deploy history to reset failure count

        Returns:
            Dict with resume details
        """
        self._validate_project(project_name)

        config = self.db.get_auto_pause_config(project_name)
        if not config or not config.get("paused"):
            raise AutoPauseError(
                code="NOT_PAUSED",
                message=f"Project '{project_name}' is not paused",
                suggestion="The project is already running",
            )

        # Clear pause state
        self.db.update_auto_pause_config(project_name, clear_pause=True)

        # Optionally reset failure history
        if reset_failures:
            self.db.delete_deploy_history_for_project(project_name)

        # Send alert
        try:
            send_alert(
                project_name=project_name,
                event_type="resume",
                event_status="success",
                data={
                    "resumed_at": datetime.utcnow().isoformat(),
                    "failures_reset": reset_failures,
                },
            )
        except Exception:
            pass  # Don't fail resume if alert fails

        return {
            "project": project_name,
            "resumed": True,
            "resumed_at": datetime.utcnow().isoformat(),
            "failures_reset": reset_failures,
        }

    def check_and_maybe_pause(self, project_name: str) -> bool:
        """Check failure threshold and pause if exceeded.

        Called after failed deploys to potentially trigger auto-pause.

        Args:
            project_name: Project name

        Returns:
            True if project was paused, False otherwise
        """
        config = self.get_config(project_name)

        # Skip if auto-pause is disabled
        if not config.enabled:
            return False

        # Skip if already paused
        if config.paused:
            return False

        # Count failures in window
        window_start = datetime.utcnow() - timedelta(minutes=config.window_minutes)
        since = window_start.isoformat()

        deploys = self.db.list_deploys(project_name, since=since, limit=100)
        failures = [d for d in deploys if not d.get("success", True)]

        # Check if threshold exceeded
        if len(failures) >= config.failure_threshold:
            reason = f"{len(failures)} failures in {config.window_minutes} minutes"
            self.pause(project_name, reason)
            return True

        return False

    def check_before_deploy(self, project_name: str) -> None:
        """Check if project is paused before deploy.

        Raises AutoPauseError if project is paused.
        """
        if self.is_paused(project_name):
            config = self.db.get_auto_pause_config(project_name)
            reason = config.get("paused_reason", "Unknown") if config else "Unknown"
            paused_at = config.get("paused_at", "") if config else ""

            raise AutoPauseError(
                code="PROJECT_PAUSED",
                message=f"Project '{project_name}' is paused: {reason}",
                suggestion=f"Run 'hostkit resume {project_name}' to continue. Paused at {paused_at}",
            )

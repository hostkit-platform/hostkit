"""Resource limits service for HostKit.

Manages CPU, memory, and disk limits for projects via systemd cgroups.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.database import get_db


@dataclass
class ResourceLimits:
    """Resource limits configuration for a project."""

    project_name: str
    cpu_quota: int | None  # Percentage (100 = 1 core)
    memory_max_mb: int | None  # Hard limit in MB (OOM kill above)
    memory_high_mb: int | None  # Soft limit in MB (throttle above)
    tasks_max: int | None  # Max processes/threads
    disk_quota_mb: int | None  # Disk quota in MB (advisory)
    enabled: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceLimits":
        """Create from database row."""
        return cls(
            project_name=data["project_name"],
            cpu_quota=data.get("cpu_quota"),
            memory_max_mb=data.get("memory_max_mb"),
            memory_high_mb=data.get("memory_high_mb"),
            tasks_max=data.get("tasks_max"),
            disk_quota_mb=data.get("disk_quota_mb"),
            enabled=bool(data.get("enabled", 1)),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    @classmethod
    def default(cls, project_name: str) -> "ResourceLimits":
        """Create default configuration with recommended limits."""
        now = datetime.utcnow().isoformat()
        return cls(
            project_name=project_name,
            cpu_quota=100,  # 1 CPU core
            memory_max_mb=512,  # 512MB hard limit
            memory_high_mb=384,  # 384MB soft limit
            tasks_max=100,  # 100 processes
            disk_quota_mb=2048,  # 2GB disk
            enabled=True,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project_name": self.project_name,
            "cpu_quota": self.cpu_quota,
            "memory_max_mb": self.memory_max_mb,
            "memory_high_mb": self.memory_high_mb,
            "tasks_max": self.tasks_max,
            "disk_quota_mb": self.disk_quota_mb,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def is_unlimited(self) -> bool:
        """Check if all limits are unset (unlimited)."""
        return (
            self.cpu_quota is None
            and self.memory_max_mb is None
            and self.memory_high_mb is None
            and self.tasks_max is None
            and self.disk_quota_mb is None
        )


@dataclass
class DiskUsageStatus:
    """Disk usage status for a project."""

    project_name: str
    home_dir_mb: int
    log_dir_mb: int
    total_mb: int
    quota_mb: int | None
    over_quota: bool
    percent_used: float | None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project_name": self.project_name,
            "home_dir_mb": self.home_dir_mb,
            "log_dir_mb": self.log_dir_mb,
            "total_mb": self.total_mb,
            "quota_mb": self.quota_mb,
            "over_quota": self.over_quota,
            "percent_used": self.percent_used,
        }


class LimitsServiceError(Exception):
    """Exception for limits service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class LimitsService:
    """Service for managing project resource limits via systemd cgroups."""

    # Default limits for new projects
    DEFAULT_CPU_QUOTA = 100  # 1 CPU core
    DEFAULT_MEMORY_MAX_MB = 512  # 512MB hard limit
    DEFAULT_MEMORY_HIGH_MB = 384  # 384MB soft limit
    DEFAULT_TASKS_MAX = 100  # 100 processes
    DEFAULT_DISK_QUOTA_MB = 2048  # 2GB disk

    def __init__(self) -> None:
        self.db = get_db()

    def _validate_project(self, project_name: str) -> dict[str, Any]:
        """Validate that the project exists."""
        proj = self.db.get_project(project_name)
        if not proj:
            raise LimitsServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return proj

    def get_limits(self, project_name: str) -> ResourceLimits | None:
        """Get resource limits for a project.

        Returns None if no limits are configured.
        """
        limits = self.db.get_resource_limits(project_name)
        if limits:
            return ResourceLimits.from_dict(limits)
        return None

    def get_limits_or_default(self, project_name: str) -> ResourceLimits:
        """Get resource limits for a project, or default if none configured."""
        limits = self.get_limits(project_name)
        if limits:
            return limits
        return ResourceLimits.default(project_name)

    def set_limits(
        self,
        project_name: str,
        cpu_quota: int | None = None,
        memory_max_mb: int | None = None,
        memory_high_mb: int | None = None,
        tasks_max: int | None = None,
        disk_quota_mb: int | None = None,
        enabled: bool | None = None,
        unlimited: bool = False,
    ) -> ResourceLimits:
        """Set resource limits for a project.

        Args:
            project_name: Project name
            cpu_quota: CPU quota as percentage (100 = 1 core)
            memory_max_mb: Hard memory limit in MB
            memory_high_mb: Soft memory limit in MB (throttle above)
            tasks_max: Max processes/threads
            disk_quota_mb: Disk quota in MB
            enabled: Enable/disable limits enforcement
            unlimited: If True, clear all limits (unlimited)

        Returns:
            Updated ResourceLimits
        """
        self._validate_project(project_name)

        # Validate values
        if cpu_quota is not None and cpu_quota <= 0:
            raise LimitsServiceError(
                code="INVALID_CPU_QUOTA",
                message="CPU quota must be positive",
                suggestion="Use a value like 50 (50% of 1 core) or 100 (1 core)",
            )
        if memory_max_mb is not None and memory_max_mb <= 0:
            raise LimitsServiceError(
                code="INVALID_MEMORY_LIMIT",
                message="Memory limit must be positive",
                suggestion="Use a value like 256, 512, or 1024 (in MB)",
            )
        if memory_high_mb is not None and memory_high_mb <= 0:
            raise LimitsServiceError(
                code="INVALID_MEMORY_LIMIT",
                message="Memory high limit must be positive",
                suggestion="Use a value like 256, 512, or 1024 (in MB)",
            )
        if tasks_max is not None and tasks_max <= 0:
            raise LimitsServiceError(
                code="INVALID_TASKS_MAX",
                message="Tasks max must be positive",
                suggestion="Use a value like 50, 100, or 200",
            )
        if disk_quota_mb is not None and disk_quota_mb <= 0:
            raise LimitsServiceError(
                code="INVALID_DISK_QUOTA",
                message="Disk quota must be positive",
                suggestion="Use a value like 1024, 2048, or 5120 (in MB)",
            )

        # Check memory_high <= memory_max if both set
        if memory_high_mb is not None and memory_max_mb is not None:
            if memory_high_mb > memory_max_mb:
                raise LimitsServiceError(
                    code="INVALID_MEMORY_CONFIG",
                    message="Memory high limit cannot exceed memory max",
                    suggestion=f"Set --memory-high to at most {memory_max_mb}M",
                )

        existing = self.db.get_resource_limits(project_name)

        if existing:
            updated = self.db.update_resource_limits(
                project_name,
                cpu_quota=cpu_quota,
                memory_max_mb=memory_max_mb,
                memory_high_mb=memory_high_mb,
                tasks_max=tasks_max,
                disk_quota_mb=disk_quota_mb,
                enabled=enabled,
                clear_limits=unlimited,
            )
        else:
            # Create new with provided values or defaults
            defaults = ResourceLimits.default(project_name)
            updated = self.db.create_resource_limits(
                project_name,
                cpu_quota=cpu_quota
                if cpu_quota is not None
                else (None if unlimited else defaults.cpu_quota),
                memory_max_mb=memory_max_mb
                if memory_max_mb is not None
                else (None if unlimited else defaults.memory_max_mb),
                memory_high_mb=memory_high_mb
                if memory_high_mb is not None
                else (None if unlimited else defaults.memory_high_mb),
                tasks_max=tasks_max
                if tasks_max is not None
                else (None if unlimited else defaults.tasks_max),
                disk_quota_mb=disk_quota_mb
                if disk_quota_mb is not None
                else (None if unlimited else defaults.disk_quota_mb),
                enabled=enabled if enabled is not None else True,
            )

        return (
            ResourceLimits.from_dict(updated) if updated else ResourceLimits.default(project_name)
        )

    def reset_limits(self, project_name: str) -> ResourceLimits:
        """Reset resource limits to defaults.

        Args:
            project_name: Project name

        Returns:
            ResourceLimits with default values
        """
        self._validate_project(project_name)

        # Delete existing and create with defaults
        self.db.delete_resource_limits(project_name)

        defaults = ResourceLimits.default(project_name)
        created = self.db.create_resource_limits(
            project_name,
            cpu_quota=defaults.cpu_quota,
            memory_max_mb=defaults.memory_max_mb,
            memory_high_mb=defaults.memory_high_mb,
            tasks_max=defaults.tasks_max,
            disk_quota_mb=defaults.disk_quota_mb,
            enabled=True,
        )

        return ResourceLimits.from_dict(created) if created else defaults

    def apply_limits(self, project_name: str) -> dict[str, Any]:
        """Apply resource limits to systemd service.

        Updates the systemd service file with resource control directives
        and reloads the service.

        Args:
            project_name: Project name

        Returns:
            Dict with apply status
        """
        self._validate_project(project_name)
        limits = self.get_limits(project_name)

        service_path = Path(f"/etc/systemd/system/hostkit-{project_name}.service")
        if not service_path.exists():
            raise LimitsServiceError(
                code="SERVICE_NOT_FOUND",
                message=f"Systemd service for '{project_name}' not found",
                suggestion="The project may not be properly configured",
            )

        # Read current service file
        content = service_path.read_text()

        # Remove existing resource control lines
        lines_to_remove = [
            r"^CPUQuota=.*$",
            r"^MemoryMax=.*$",
            r"^MemoryHigh=.*$",
            r"^TasksMax=.*$",
        ]
        for pattern in lines_to_remove:
            content = re.sub(pattern, "", content, flags=re.MULTILINE)

        # Clean up extra blank lines
        content = re.sub(r"\n{3,}", "\n\n", content)

        # Build new resource control section
        resource_lines = []
        if limits and limits.enabled:
            if limits.cpu_quota is not None:
                resource_lines.append(f"CPUQuota={limits.cpu_quota}%")
            if limits.memory_max_mb is not None:
                resource_lines.append(f"MemoryMax={limits.memory_max_mb}M")
            if limits.memory_high_mb is not None:
                resource_lines.append(f"MemoryHigh={limits.memory_high_mb}M")
            if limits.tasks_max is not None:
                resource_lines.append(f"TasksMax={limits.tasks_max}")

        # Insert resource lines after [Service] section
        if resource_lines:
            resource_block = "\n".join(resource_lines)
            # Find [Service] section and add after first line following it
            match = re.search(r"(\[Service\]\n[^\n]*\n)", content)
            if match:
                insert_pos = match.end()
                content = (
                    content[:insert_pos]
                    + "# Resource Limits (managed by HostKit)\n"
                    + resource_block
                    + "\n"
                    + content[insert_pos:]
                )

        # Write updated service file
        service_path.write_text(content)

        # Reload systemd and restart service
        try:
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
            )
            # Restart service to apply limits
            subprocess.run(
                ["systemctl", "restart", f"hostkit-{project_name}"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise LimitsServiceError(
                code="SYSTEMD_ERROR",
                message=f"Failed to apply limits: {e.stderr.decode() if e.stderr else str(e)}",
                suggestion="Check systemd logs for details",
            )

        return {
            "project": project_name,
            "applied": True,
            "limits": limits.to_dict() if limits else None,
            "service": f"hostkit-{project_name}",
        }

    def check_disk_usage(self, project_name: str) -> DiskUsageStatus:
        """Check disk usage for a project.

        Args:
            project_name: Project name

        Returns:
            DiskUsageStatus with usage details
        """
        self._validate_project(project_name)

        home_path = Path(f"/home/{project_name}")
        log_path = Path(f"/var/log/projects/{project_name}")

        home_size = self._get_directory_size_mb(home_path)
        log_size = self._get_directory_size_mb(log_path)
        total = home_size + log_size

        limits = self.get_limits(project_name)
        quota = limits.disk_quota_mb if limits else None

        over_quota = False
        percent_used = None
        if quota is not None and quota > 0:
            over_quota = total > quota
            percent_used = round((total / quota) * 100, 1)

        return DiskUsageStatus(
            project_name=project_name,
            home_dir_mb=home_size,
            log_dir_mb=log_size,
            total_mb=total,
            quota_mb=quota,
            over_quota=over_quota,
            percent_used=percent_used,
        )

    def _get_directory_size_mb(self, path: Path) -> int:
        """Get directory size in MB using du."""
        if not path.exists():
            return 0

        try:
            result = subprocess.run(
                ["du", "-sm", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                size_str = result.stdout.strip().split()[0]
                return int(size_str)
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass

        return 0

    def check_disk_before_deploy(self, project_name: str) -> dict[str, Any] | None:
        """Check disk usage before deploy and warn if over quota.

        Args:
            project_name: Project name

        Returns:
            Warning dict if over quota, None otherwise
        """
        try:
            status = self.check_disk_usage(project_name)
            if status.over_quota:
                return {
                    "warning": "OVER_DISK_QUOTA",
                    "message": (
                        f"Project exceeds disk quota ({status.total_mb}MB / {status.quota_mb}MB)"
                    ),
                    "suggestion": "Consider cleaning up files or increasing the quota",
                    "usage": status.to_dict(),
                }
        except LimitsServiceError:
            pass

        return None

    def get_effective_limits_for_service(self, project_name: str) -> dict[str, str]:
        """Get limits formatted for systemd service file.

        Args:
            project_name: Project name

        Returns:
            Dict with systemd directives as key-value pairs
        """
        limits = self.get_limits(project_name)
        if not limits or not limits.enabled:
            return {}

        directives = {}
        if limits.cpu_quota is not None:
            directives["CPUQuota"] = f"{limits.cpu_quota}%"
        if limits.memory_max_mb is not None:
            directives["MemoryMax"] = f"{limits.memory_max_mb}M"
        if limits.memory_high_mb is not None:
            directives["MemoryHigh"] = f"{limits.memory_high_mb}M"
        if limits.tasks_max is not None:
            directives["TasksMax"] = str(limits.tasks_max)

        return directives

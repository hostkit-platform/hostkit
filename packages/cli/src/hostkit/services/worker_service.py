"""Celery worker management service for HostKit using systemd."""

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class Worker:
    """Information about a Celery worker."""

    id: str
    project: str
    worker_name: str
    concurrency: int
    queues: str | None
    app_module: str
    loglevel: str
    enabled: bool
    created_at: str
    updated_at: str
    created_by: str | None
    service_active: bool = False
    service_enabled: bool = False

    @classmethod
    def from_db(cls, row: dict[str, Any]) -> "Worker":
        """Create from database row."""
        return cls(
            id=row["id"],
            project=row["project"],
            worker_name=row["worker_name"],
            concurrency=row.get("concurrency", 2),
            queues=row.get("queues"),
            app_module=row.get("app_module", "app"),
            loglevel=row.get("loglevel", "info"),
            enabled=bool(row.get("enabled", 1)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=row.get("created_by"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "project": self.project,
            "worker_name": self.worker_name,
            "concurrency": self.concurrency,
            "queues": self.queues,
            "app_module": self.app_module,
            "loglevel": self.loglevel,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "service_active": self.service_active,
            "service_enabled": self.service_enabled,
        }


@dataclass
class CeleryBeat:
    """Information about Celery beat scheduler."""

    project: str
    enabled: bool
    schedule_file: str
    created_at: str
    updated_at: str
    service_active: bool = False
    service_enabled: bool = False

    @classmethod
    def from_db(cls, row: dict[str, Any]) -> "CeleryBeat":
        """Create from database row."""
        return cls(
            project=row["project"],
            enabled=bool(row.get("enabled", 1)),
            schedule_file=row.get("schedule_file", "celerybeat-schedule"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "project": self.project,
            "enabled": self.enabled,
            "schedule_file": self.schedule_file,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "service_active": self.service_active,
            "service_enabled": self.service_enabled,
        }


class WorkerError(Exception):
    """Exception for worker service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class WorkerService:
    """Service for managing Celery workers using systemd."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.templates_dir = Path("/var/lib/hostkit/templates")
        self.systemd_dir = Path("/etc/systemd/system")

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists."""
        project_info = self.db.get_project(project)
        if not project_info:
            raise WorkerError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return project_info

    def _validate_worker_name(self, name: str) -> None:
        """Validate worker name format."""
        if not re.match(r"^[a-z][a-z0-9-]*$", name):
            raise WorkerError(
                code="INVALID_WORKER_NAME",
                message=f"Invalid worker name '{name}'",
                suggestion=(
                    "Worker name must start with a letter and"
                    " contain only lowercase letters,"
                    " numbers, and hyphens"
                ),
            )
        if len(name) > 50:
            raise WorkerError(
                code="WORKER_NAME_TOO_LONG",
                message="Worker name must be 50 characters or less",
            )

    def _service_name(self, project: str, worker_name: str) -> str:
        """Generate systemd service unit name for worker."""
        return f"hostkit-{project}-worker-{worker_name}"

    def _beat_service_name(self, project: str) -> str:
        """Generate systemd service unit name for beat."""
        return f"hostkit-{project}-beat"

    def _load_template(self, template_name: str) -> Template:
        """Load a Jinja2 template."""
        template_path = self.templates_dir / template_name
        if not template_path.exists():
            raise WorkerError(
                code="TEMPLATE_NOT_FOUND",
                message=f"Template '{template_name}' not found",
                suggestion="Ensure templates are synced to /var/lib/hostkit/templates/",
            )
        return Template(template_path.read_text())

    def _get_service_status(self, service_name: str) -> tuple[bool, bool]:
        """Get service active and enabled status.

        Returns:
            Tuple of (is_active, is_enabled)
        """
        active = False
        enabled = False

        try:
            # Check active
            result = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            active = result.returncode == 0

            # Check enabled
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            enabled = result.stdout.strip() == "enabled"

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return active, enabled

    def _create_worker_systemd_unit(self, worker: Worker) -> None:
        """Create systemd service unit file for worker."""
        service_name = self._service_name(worker.project, worker.worker_name)

        # Load template
        template = self._load_template("celery-worker.service.j2")

        # Render service file
        service_content = template.render(
            project_name=worker.project,
            worker_name=worker.worker_name,
            app_module=worker.app_module,
            loglevel=worker.loglevel,
            concurrency=worker.concurrency,
            queues=worker.queues,
        )

        # Write unit file
        service_path = self.systemd_dir / f"{service_name}.service"
        service_path.write_text(service_content)

        # Reload systemd
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            timeout=10,
        )

    def _create_beat_systemd_unit(
        self, project: str, beat: CeleryBeat, app_module: str = "app", loglevel: str = "info"
    ) -> None:
        """Create systemd service unit file for beat scheduler."""
        service_name = self._beat_service_name(project)

        # Load template
        template = self._load_template("celery-beat.service.j2")

        # Render service file
        service_content = template.render(
            project_name=project,
            app_module=app_module,
            loglevel=loglevel,
            schedule_file=beat.schedule_file,
        )

        # Write unit file
        service_path = self.systemd_dir / f"{service_name}.service"
        service_path.write_text(service_content)

        # Reload systemd
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            timeout=10,
        )

    def _remove_systemd_unit(self, service_name: str) -> None:
        """Remove systemd service unit file."""
        # Stop and disable service first
        try:
            subprocess.run(
                ["systemctl", "stop", f"{service_name}.service"],
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["systemctl", "disable", f"{service_name}.service"],
                capture_output=True,
                timeout=10,
            )
        except subprocess.SubprocessError:
            pass

        # Remove unit file
        service_path = self.systemd_dir / f"{service_name}.service"

        if service_path.exists():
            service_path.unlink()

        # Reload systemd
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            timeout=10,
        )

    def add_worker(
        self,
        project: str,
        worker_name: str = "default",
        concurrency: int = 2,
        queues: str | None = None,
        app_module: str = "app",
        loglevel: str = "info",
    ) -> Worker:
        """Add a new Celery worker.

        Args:
            project: Project name
            worker_name: Worker name (default: "default")
            concurrency: Number of worker processes
            queues: Comma-separated list of queues (optional)
            app_module: Celery app module name
            loglevel: Log level (debug, info, warning, error)

        Returns:
            Created Worker
        """
        self._validate_project(project)
        self._validate_worker_name(worker_name)

        # Check if worker already exists
        existing = self.db.get_worker(project, worker_name)
        if existing:
            raise WorkerError(
                code="WORKER_EXISTS",
                message=f"Worker '{worker_name}' already exists for project '{project}'",
                suggestion=(
                    f"Use 'hostkit worker remove {project} --name {worker_name}' to delete it first"
                ),
            )

        # Validate concurrency
        if concurrency < 1 or concurrency > 32:
            raise WorkerError(
                code="INVALID_CONCURRENCY",
                message="Concurrency must be between 1 and 32",
            )

        # Validate loglevel
        valid_levels = ["debug", "info", "warning", "error", "critical"]
        if loglevel.lower() not in valid_levels:
            raise WorkerError(
                code="INVALID_LOGLEVEL",
                message=f"Invalid log level '{loglevel}'",
                suggestion=f"Valid levels: {', '.join(valid_levels)}",
            )

        # Get current user for created_by
        created_by = os.environ.get("SUDO_USER") or os.environ.get("USER", "root")

        # Create database record
        worker_id = str(uuid.uuid4())
        worker_data = self.db.create_worker(
            worker_id=worker_id,
            project=project,
            worker_name=worker_name,
            concurrency=concurrency,
            queues=queues,
            app_module=app_module,
            loglevel=loglevel.lower(),
            created_by=created_by,
        )

        worker = Worker.from_db(worker_data)

        # Create systemd unit
        self._create_worker_systemd_unit(worker)

        # Enable and start service if worker is enabled
        if worker.enabled:
            service_name = self._service_name(project, worker_name)
            subprocess.run(
                ["systemctl", "enable", f"{service_name}.service"],
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["systemctl", "start", f"{service_name}.service"],
                capture_output=True,
                timeout=10,
            )

        # Update service status
        service_name = self._service_name(project, worker_name)
        worker.service_active, worker.service_enabled = self._get_service_status(service_name)

        return worker

    def remove_worker(self, project: str, worker_name: str = "default") -> dict[str, Any]:
        """Remove a Celery worker.

        Args:
            project: Project name
            worker_name: Worker name

        Returns:
            Result dict with removed worker info
        """
        self._validate_project(project)

        worker_data = self.db.get_worker(project, worker_name)
        if not worker_data:
            raise WorkerError(
                code="WORKER_NOT_FOUND",
                message=f"Worker '{worker_name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit worker list {project}' to see available workers",
            )

        # Remove systemd unit
        service_name = self._service_name(project, worker_name)
        self._remove_systemd_unit(service_name)

        # Delete from database
        self.db.delete_worker(project, worker_name)

        return {
            "project": project,
            "worker_name": worker_name,
            "removed": True,
        }

    def list_workers(self, project: str) -> list[Worker]:
        """List all workers for a project.

        Args:
            project: Project name

        Returns:
            List of Worker objects
        """
        self._validate_project(project)

        workers = []
        for row in self.db.list_workers(project):
            worker = Worker.from_db(row)
            # Get service status
            service_name = self._service_name(project, worker.worker_name)
            worker.service_active, worker.service_enabled = self._get_service_status(service_name)
            workers.append(worker)

        return workers

    def get_worker(self, project: str, worker_name: str = "default") -> Worker:
        """Get a specific worker.

        Args:
            project: Project name
            worker_name: Worker name

        Returns:
            Worker object
        """
        self._validate_project(project)

        worker_data = self.db.get_worker(project, worker_name)
        if not worker_data:
            raise WorkerError(
                code="WORKER_NOT_FOUND",
                message=f"Worker '{worker_name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit worker list {project}' to see available workers",
            )

        worker = Worker.from_db(worker_data)
        service_name = self._service_name(project, worker_name)
        worker.service_active, worker.service_enabled = self._get_service_status(service_name)

        return worker

    def start_worker(self, project: str, worker_name: str = "default") -> Worker:
        """Start a Celery worker.

        Args:
            project: Project name
            worker_name: Worker name

        Returns:
            Updated Worker
        """
        self.get_worker(project, worker_name)

        service_name = self._service_name(project, worker_name)

        # Start service
        result = subprocess.run(
            ["systemctl", "start", f"{service_name}.service"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise WorkerError(
                code="START_FAILED",
                message=f"Failed to start worker '{worker_name}'",
                suggestion=f"Check logs with: hostkit worker logs {project} --name {worker_name}",
            )

        # Return updated worker
        return self.get_worker(project, worker_name)

    def stop_worker(self, project: str, worker_name: str = "default") -> Worker:
        """Stop a Celery worker.

        Args:
            project: Project name
            worker_name: Worker name

        Returns:
            Updated Worker
        """
        self.get_worker(project, worker_name)

        service_name = self._service_name(project, worker_name)

        # Stop service
        subprocess.run(
            ["systemctl", "stop", f"{service_name}.service"],
            capture_output=True,
            timeout=30,
        )

        # Return updated worker
        return self.get_worker(project, worker_name)

    def restart_worker(self, project: str, worker_name: str = "default") -> Worker:
        """Restart a Celery worker.

        Args:
            project: Project name
            worker_name: Worker name

        Returns:
            Updated Worker
        """
        self.get_worker(project, worker_name)

        service_name = self._service_name(project, worker_name)

        # Restart service
        result = subprocess.run(
            ["systemctl", "restart", f"{service_name}.service"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise WorkerError(
                code="RESTART_FAILED",
                message=f"Failed to restart worker '{worker_name}'",
                suggestion=f"Check logs with: hostkit worker logs {project} --name {worker_name}",
            )

        # Return updated worker
        return self.get_worker(project, worker_name)

    def scale_worker(self, project: str, concurrency: int, worker_name: str = "default") -> Worker:
        """Scale a worker's concurrency.

        Args:
            project: Project name
            concurrency: New concurrency level
            worker_name: Worker name

        Returns:
            Updated Worker
        """
        worker = self.get_worker(project, worker_name)

        if concurrency < 1 or concurrency > 32:
            raise WorkerError(
                code="INVALID_CONCURRENCY",
                message="Concurrency must be between 1 and 32",
            )

        # Update database
        self.db.update_worker(project, worker_name, concurrency=concurrency)

        # Get updated worker
        worker = self.get_worker(project, worker_name)

        # Update systemd unit
        self._create_worker_systemd_unit(worker)

        # Restart if running
        if worker.service_active:
            self.restart_worker(project, worker_name)

        return self.get_worker(project, worker_name)

    def get_worker_logs(
        self,
        project: str,
        worker_name: str = "default",
        lines: int = 50,
        follow: bool = False,
    ) -> str | None:
        """Get logs for a Celery worker.

        Args:
            project: Project name
            worker_name: Worker name
            lines: Number of lines to return
            follow: If True, follow logs (blocking)

        Returns:
            Log content or None if no logs
        """
        self.get_worker(project, worker_name)  # Validate worker exists

        log_path = Path(f"/var/log/projects/{project}/celery-{worker_name}.log")

        if not log_path.exists():
            return None

        if follow:
            # Use tail -f (this will block)
            subprocess.run(
                ["tail", "-f", "-n", str(lines), str(log_path)],
            )
            return None

        # Return last N lines
        try:
            result = subprocess.run(
                ["tail", "-n", str(lines), str(log_path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout if result.returncode == 0 else None
        except subprocess.SubprocessError:
            return None

    # Beat scheduler methods
    def enable_beat(
        self, project: str, app_module: str = "app", loglevel: str = "info"
    ) -> CeleryBeat:
        """Enable Celery beat scheduler for a project.

        Args:
            project: Project name
            app_module: Celery app module name
            loglevel: Log level

        Returns:
            CeleryBeat object
        """
        self._validate_project(project)

        # Check if beat already exists
        existing = self.db.get_celery_beat(project)
        if existing:
            # Update and enable
            self.db.update_celery_beat(project, enabled=True)
        else:
            # Create new beat record
            self.db.create_celery_beat(project)

        beat_data = self.db.get_celery_beat(project)
        beat = CeleryBeat.from_db(beat_data)

        # Create systemd unit
        self._create_beat_systemd_unit(project, beat, app_module, loglevel)

        # Enable and start service
        service_name = self._beat_service_name(project)
        subprocess.run(
            ["systemctl", "enable", f"{service_name}.service"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["systemctl", "start", f"{service_name}.service"],
            capture_output=True,
            timeout=10,
        )

        # Update service status
        beat.service_active, beat.service_enabled = self._get_service_status(service_name)

        return beat

    def disable_beat(self, project: str) -> CeleryBeat:
        """Disable Celery beat scheduler for a project.

        Args:
            project: Project name

        Returns:
            Updated CeleryBeat object
        """
        self._validate_project(project)

        beat_data = self.db.get_celery_beat(project)
        if not beat_data:
            raise WorkerError(
                code="BEAT_NOT_FOUND",
                message=f"Beat scheduler not found for project '{project}'",
                suggestion=f"Use 'hostkit worker beat enable {project}' to enable it",
            )

        # Stop and disable service
        service_name = self._beat_service_name(project)
        subprocess.run(
            ["systemctl", "stop", f"{service_name}.service"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["systemctl", "disable", f"{service_name}.service"],
            capture_output=True,
            timeout=10,
        )

        # Update database
        self.db.update_celery_beat(project, enabled=False)

        # Return updated beat
        beat_data = self.db.get_celery_beat(project)
        beat = CeleryBeat.from_db(beat_data)
        beat.service_active, beat.service_enabled = self._get_service_status(service_name)

        return beat

    def get_beat_status(self, project: str) -> CeleryBeat | None:
        """Get Celery beat status for a project.

        Args:
            project: Project name

        Returns:
            CeleryBeat object or None if not configured
        """
        self._validate_project(project)

        beat_data = self.db.get_celery_beat(project)
        if not beat_data:
            return None

        beat = CeleryBeat.from_db(beat_data)
        service_name = self._beat_service_name(project)
        beat.service_active, beat.service_enabled = self._get_service_status(service_name)

        return beat

    def get_beat_logs(
        self,
        project: str,
        lines: int = 50,
        follow: bool = False,
    ) -> str | None:
        """Get logs for Celery beat scheduler.

        Args:
            project: Project name
            lines: Number of lines to return
            follow: If True, follow logs (blocking)

        Returns:
            Log content or None if no logs
        """
        self._validate_project(project)

        beat_data = self.db.get_celery_beat(project)
        if not beat_data:
            raise WorkerError(
                code="BEAT_NOT_FOUND",
                message=f"Beat scheduler not found for project '{project}'",
                suggestion=f"Use 'hostkit worker beat enable {project}' to enable it",
            )

        log_path = Path(f"/var/log/projects/{project}/celery-beat.log")

        if not log_path.exists():
            return None

        if follow:
            # Use tail -f (this will block)
            subprocess.run(
                ["tail", "-f", "-n", str(lines), str(log_path)],
            )
            return None

        # Return last N lines
        try:
            result = subprocess.run(
                ["tail", "-n", str(lines), str(log_path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout if result.returncode == 0 else None
        except subprocess.SubprocessError:
            return None

    def get_worker_status(self, project: str) -> dict[str, Any]:
        """Get comprehensive worker status for a project.

        Args:
            project: Project name

        Returns:
            Status dict with workers and beat info
        """
        self._validate_project(project)

        workers = self.list_workers(project)
        beat = self.get_beat_status(project)

        return {
            "project": project,
            "workers": [w.to_dict() for w in workers],
            "beat": beat.to_dict() if beat else None,
            "worker_count": len(workers),
            "active_workers": sum(1 for w in workers if w.service_active),
        }

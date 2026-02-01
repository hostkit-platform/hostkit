"""Systemd service management for HostKit."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class ServiceInfo:
    """Information about a systemd service."""

    name: str
    display_name: str
    service_type: str  # "app" or "worker"
    project: str
    status: str  # "running", "stopped", "failed"
    enabled: bool
    pid: int | None = None
    memory: str | None = None
    uptime: str | None = None


class ServiceError(Exception):
    """Base exception for service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Celery service template
# WorkingDirectory is /home/{{ project_name }} so celery -A app can find the app/ module
CELERY_SERVICE_TEMPLATE = """[Unit]
Description=Celery Worker for {{ project_name }}
After=network.target redis.service
Wants=redis.service

[Service]
Type=forking
User={{ project_name }}
Group={{ project_name }}
WorkingDirectory=/home/{{ project_name }}
EnvironmentFile=/home/{{ project_name }}/.env
ExecStart=/home/{{ project_name }}/venv/bin/celery -A {{ app_module }} worker --loglevel=info --pidfile=/home/{{ project_name }}/celery.pid --concurrency={{ concurrency }}
ExecStop=/bin/kill -TERM $MAINPID
PIDFile=/home/{{ project_name }}/celery.pid
Restart=always
RestartSec=5
StandardOutput=append:/var/log/projects/{{ project_name }}/celery.log
StandardError=append:/var/log/projects/{{ project_name }}/celery-error.log

[Install]
WantedBy=multi-user.target
"""


class ServiceService:
    """Service for managing systemd services for HostKit projects."""

    def __init__(self) -> None:
        self.config = get_config()
        self.hostkit_db = get_db()

    def _service_name(self, project: str, service_type: str = "app") -> str:
        """Generate systemd service name."""
        if service_type == "app":
            return f"hostkit-{project}"
        return f"hostkit-{project}-{service_type}"

    def _get_service_status(self, service_name: str) -> tuple[str, bool]:
        """Get service status and enabled state."""
        try:
            # Check active status
            result = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            status_raw = result.stdout.strip()
            if status_raw == "active":
                status = "running"
            elif status_raw == "inactive":
                status = "stopped"
            elif status_raw == "failed":
                status = "failed"
            else:
                status = "stopped"

            # Check enabled status
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            enabled = result.stdout.strip() == "enabled"

            return status, enabled
        except (subprocess.SubprocessError, FileNotFoundError):
            return "stopped", False

    def _get_service_details(self, service_name: str) -> dict[str, Any]:
        """Get detailed service information."""
        details: dict[str, Any] = {"pid": None, "memory": None, "uptime": None}

        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    f"{service_name}.service",
                    "--property=MainPID,MemoryCurrent,ActiveEnterTimestamp",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key == "MainPID" and value != "0":
                            details["pid"] = int(value)
                        elif key == "MemoryCurrent" and value != "[not set]":
                            try:
                                mem_bytes = int(value)
                                if mem_bytes >= 1024 * 1024 * 1024:
                                    details["memory"] = f"{mem_bytes / (1024**3):.1f} GB"
                                elif mem_bytes >= 1024 * 1024:
                                    details["memory"] = f"{mem_bytes / (1024**2):.1f} MB"
                                elif mem_bytes >= 1024:
                                    details["memory"] = f"{mem_bytes / 1024:.1f} KB"
                                else:
                                    details["memory"] = f"{mem_bytes} B"
                            except ValueError:
                                pass
                        elif key == "ActiveEnterTimestamp" and value:
                            details["uptime"] = value

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return details

    def list_services(self, project: str | None = None) -> list[ServiceInfo]:
        """List all HostKit services, optionally filtered by project."""
        services = []

        # Get all projects
        if project:
            projects = [self.hostkit_db.get_project(project)]
            if not projects[0]:
                raise ServiceError(
                    code="PROJECT_NOT_FOUND",
                    message=f"Project '{project}' does not exist",
                    suggestion="Run 'hostkit project list' to see available projects",
                )
        else:
            projects = self.hostkit_db.list_projects()

        for proj in projects:
            if not proj:
                continue

            proj_name = proj["name"]

            # Check main app service
            app_service = self._service_name(proj_name, "app")
            if Path(f"/etc/systemd/system/{app_service}.service").exists():
                status, enabled = self._get_service_status(app_service)
                details = self._get_service_details(app_service)

                services.append(
                    ServiceInfo(
                        name=app_service,
                        display_name=f"{proj_name} (app)",
                        service_type="app",
                        project=proj_name,
                        status=status,
                        enabled=enabled,
                        pid=details["pid"],
                        memory=details["memory"],
                        uptime=details["uptime"],
                    )
                )

            # Check worker service
            worker_service = self._service_name(proj_name, "worker")
            if Path(f"/etc/systemd/system/{worker_service}.service").exists():
                status, enabled = self._get_service_status(worker_service)
                details = self._get_service_details(worker_service)

                services.append(
                    ServiceInfo(
                        name=worker_service,
                        display_name=f"{proj_name} (worker)",
                        service_type="worker",
                        project=proj_name,
                        status=status,
                        enabled=enabled,
                        pid=details["pid"],
                        memory=details["memory"],
                        uptime=details["uptime"],
                    )
                )

        return services

    def get_service(self, name: str) -> ServiceInfo:
        """Get information about a specific service."""
        # Handle shorthand names
        if not name.startswith("hostkit-"):
            # Check if it's a project name
            project = self.hostkit_db.get_project(name)
            if project:
                name = f"hostkit-{name}"

        service_path = Path(f"/etc/systemd/system/{name}.service")
        if not service_path.exists():
            raise ServiceError(
                code="SERVICE_NOT_FOUND",
                message=f"Service '{name}' not found",
                suggestion="Run 'hostkit service list' to see available services",
            )

        # Parse service name
        parts = name.replace("hostkit-", "").split("-")
        project_name = parts[0]
        service_type = parts[1] if len(parts) > 1 else "app"

        status, enabled = self._get_service_status(name)
        details = self._get_service_details(name)

        return ServiceInfo(
            name=name,
            display_name=f"{project_name} ({service_type})",
            service_type=service_type,
            project=project_name,
            status=status,
            enabled=enabled,
            pid=details["pid"],
            memory=details["memory"],
            uptime=details["uptime"],
        )

    def start(self, name: str) -> dict[str, Any]:
        """Start a service."""
        service = self.get_service(name)

        if service.status == "running":
            raise ServiceError(
                code="SERVICE_ALREADY_RUNNING",
                message=f"Service '{service.name}' is already running",
                suggestion="Use 'hostkit service restart' to restart it",
            )

        try:
            subprocess.run(
                ["systemctl", "start", f"{service.name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise ServiceError(
                code="SERVICE_START_FAILED",
                message=f"Failed to start service: {e.stderr.decode() if e.stderr else 'unknown error'}",
                suggestion="Check logs with 'hostkit service logs'",
            )

        # Get updated status
        new_service = self.get_service(service.name)
        return {
            "name": service.name,
            "project": service.project,
            "status": new_service.status,
            "pid": new_service.pid,
        }

    def stop(self, name: str) -> dict[str, Any]:
        """Stop a service."""
        service = self.get_service(name)

        if service.status == "stopped":
            raise ServiceError(
                code="SERVICE_ALREADY_STOPPED",
                message=f"Service '{service.name}' is already stopped",
            )

        try:
            subprocess.run(
                ["systemctl", "stop", f"{service.name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise ServiceError(
                code="SERVICE_STOP_FAILED",
                message=f"Failed to stop service: {e.stderr.decode() if e.stderr else 'unknown error'}",
            )

        return {
            "name": service.name,
            "project": service.project,
            "status": "stopped",
        }

    def restart(self, name: str) -> dict[str, Any]:
        """Restart a service."""
        service = self.get_service(name)

        try:
            subprocess.run(
                ["systemctl", "restart", f"{service.name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise ServiceError(
                code="SERVICE_RESTART_FAILED",
                message=f"Failed to restart service: {e.stderr.decode() if e.stderr else 'unknown error'}",
                suggestion="Check logs with 'hostkit service logs'",
            )

        # Get updated status
        new_service = self.get_service(service.name)
        return {
            "name": service.name,
            "project": service.project,
            "status": new_service.status,
            "pid": new_service.pid,
        }

    def enable(self, name: str) -> dict[str, Any]:
        """Enable a service to start on boot."""
        service = self.get_service(name)

        if service.enabled:
            raise ServiceError(
                code="SERVICE_ALREADY_ENABLED",
                message=f"Service '{service.name}' is already enabled",
            )

        try:
            subprocess.run(
                ["systemctl", "enable", f"{service.name}.service"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise ServiceError(
                code="SERVICE_ENABLE_FAILED",
                message=f"Failed to enable service: {e.stderr.decode() if e.stderr else 'unknown error'}",
            )

        return {
            "name": service.name,
            "project": service.project,
            "enabled": True,
        }

    def disable(self, name: str) -> dict[str, Any]:
        """Disable a service from starting on boot."""
        service = self.get_service(name)

        if not service.enabled:
            raise ServiceError(
                code="SERVICE_ALREADY_DISABLED",
                message=f"Service '{service.name}' is already disabled",
            )

        try:
            subprocess.run(
                ["systemctl", "disable", f"{service.name}.service"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise ServiceError(
                code="SERVICE_DISABLE_FAILED",
                message=f"Failed to disable service: {e.stderr.decode() if e.stderr else 'unknown error'}",
            )

        return {
            "name": service.name,
            "project": service.project,
            "enabled": False,
        }

    def get_logs(
        self,
        name: str,
        lines: int = 100,
        follow: bool = False,
        systemd: bool = False,
        error_only: bool = False,
    ) -> subprocess.Popen | str:
        """Get service logs.

        If follow=True, returns a Popen object for streaming.
        Otherwise, returns log output as string.

        Args:
            name: Service or project name
            lines: Number of lines to return
            follow: Stream logs in real-time
            systemd: Show journalctl/systemd logs instead of app logs
            error_only: Show only error.log (stderr) instead of both

        By default, reads from log files at /var/log/projects/{project}/ which contain
        the actual application output (stdout/stderr), not just systemd messages.
        """
        service = self.get_service(name)
        project = service.project

        # Log files location
        log_dir = Path(f"/var/log/projects/{project}")
        app_log = log_dir / "app.log"
        error_log = log_dir / "error.log"

        # Systemd/journalctl mode
        if systemd:
            if follow:
                cmd = ["journalctl", "-u", f"{service.name}.service", "-n", str(lines), "-f"]
                return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            else:
                try:
                    result = subprocess.run(
                        ["journalctl", "-u", f"{service.name}.service", "-n", str(lines), "--no-pager"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    return result.stdout or "(No systemd logs available)"
                except subprocess.SubprocessError as e:
                    raise ServiceError(
                        code="LOG_FETCH_FAILED",
                        message=f"Failed to fetch systemd logs: {e}",
                    )

        # Error-only mode
        if error_only:
            if follow:
                if error_log.exists():
                    cmd = ["tail", "-f", "-n", str(lines), str(error_log)]
                else:
                    return subprocess.Popen(
                        ["echo", "(No error.log file exists yet)"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    )
                return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            else:
                try:
                    if error_log.exists():
                        result = subprocess.run(
                            ["tail", "-n", str(lines), str(error_log)],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        return result.stdout or "(error.log is empty)"
                    else:
                        return "(No error.log file exists yet)"
                except subprocess.SubprocessError as e:
                    raise ServiceError(
                        code="LOG_FETCH_FAILED",
                        message=f"Failed to fetch error logs: {e}",
                    )

        # Default: app logs (stdout + stderr)
        if follow:
            # Use tail -f on both log files
            # Prefer app.log as it has most output, but include errors too
            if app_log.exists():
                cmd = ["tail", "-f", "-n", str(lines), str(app_log)]
            elif error_log.exists():
                cmd = ["tail", "-f", "-n", str(lines), str(error_log)]
            else:
                # Fall back to journalctl if no log files exist yet
                cmd = ["journalctl", "-u", f"{service.name}.service", "-n", str(lines), "-f"]

            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        try:
            output_parts = []

            # Read app.log (stdout)
            if app_log.exists():
                result = subprocess.run(
                    ["tail", "-n", str(lines), str(app_log)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.stdout.strip():
                    output_parts.append(f"=== {app_log} ===\n{result.stdout}")

            # Read error.log (stderr)
            if error_log.exists():
                result = subprocess.run(
                    ["tail", "-n", str(lines), str(error_log)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.stdout.strip():
                    output_parts.append(f"=== {error_log} ===\n{result.stdout}")

            # If no log files, fall back to journalctl for systemd messages
            if not output_parts:
                result = subprocess.run(
                    ["journalctl", "-u", f"{service.name}.service", "-n", str(lines), "--no-pager"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return result.stdout or "(No logs available)"

            return "\n".join(output_parts)

        except subprocess.SubprocessError as e:
            raise ServiceError(
                code="LOG_FETCH_FAILED",
                message=f"Failed to fetch logs: {e}",
            )

    def create_worker(
        self, project: str, app_module: str = "app", concurrency: int = 2
    ) -> dict[str, Any]:
        """Create a Celery worker service for a project."""
        # Verify project exists
        proj = self.hostkit_db.get_project(project)
        if not proj:
            raise ServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if worker already exists
        worker_service = self._service_name(project, "worker")
        if Path(f"/etc/systemd/system/{worker_service}.service").exists():
            raise ServiceError(
                code="WORKER_EXISTS",
                message=f"Worker service already exists for '{project}'",
                suggestion="Use 'hostkit service start' to start the existing worker",
            )

        # Check project has Python runtime (Celery is Python-only)
        if proj.get("runtime") != "python":
            raise ServiceError(
                code="INVALID_RUNTIME",
                message="Celery workers are only supported for Python projects",
                suggestion="Create a Python project with 'hostkit project create --python'",
            )

        # Generate service file from template
        template = Template(CELERY_SERVICE_TEMPLATE)
        service_content = template.render(
            project_name=project,
            app_module=app_module,
            concurrency=concurrency,
        )

        # Write service file
        service_path = Path(f"/etc/systemd/system/{worker_service}.service")
        service_path.write_text(service_content)

        # Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "celery.log").touch()
        (log_dir / "celery-error.log").touch()

        # Set ownership on log files
        subprocess.run(
            ["chown", "-R", f"{project}:{project}", str(log_dir)],
            check=True,
            capture_output=True,
        )

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

        return {
            "service": worker_service,
            "project": project,
            "app_module": app_module,
            "concurrency": concurrency,
            "status": "created",
            "suggestion": f"Start with 'hostkit service start {worker_service}'",
        }

    def delete_worker(self, project: str, force: bool = False) -> dict[str, Any]:
        """Delete a Celery worker service."""
        if not force:
            raise ServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a worker service requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        worker_service = self._service_name(project, "worker")
        service_path = Path(f"/etc/systemd/system/{worker_service}.service")

        if not service_path.exists():
            raise ServiceError(
                code="WORKER_NOT_FOUND",
                message=f"Worker service does not exist for '{project}'",
            )

        # Stop service if running
        try:
            subprocess.run(
                ["systemctl", "stop", f"{worker_service}.service"],
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["systemctl", "disable", f"{worker_service}.service"],
                capture_output=True,
            )
        except subprocess.SubprocessError:
            pass

        # Remove service file
        service_path.unlink()

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

        return {
            "service": worker_service,
            "project": project,
            "deleted": True,
        }

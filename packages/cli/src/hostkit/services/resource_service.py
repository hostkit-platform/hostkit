"""Resource monitoring service for HostKit projects."""

import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from hostkit.database import get_db


@dataclass
class ProjectResources:
    """Resource metrics for a project."""

    project: str
    timestamp: str
    # Process metrics
    process_count: int = 0
    main_pid: int | None = None
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    memory_vms_bytes: int | None = None
    memory_percent: float | None = None
    # Disk metrics
    home_dir_bytes: int = 0
    log_dir_bytes: int = 0
    backup_dir_bytes: int = 0
    total_disk_bytes: int = 0
    # Database metrics
    database_size_bytes: int | None = None
    database_connections: int | None = None
    database_name: str | None = None
    # Alerts
    alerts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "timestamp": self.timestamp,
            "process": {
                "count": self.process_count,
                "main_pid": self.main_pid,
                "cpu_percent": self.cpu_percent,
                "memory_rss_bytes": self.memory_rss_bytes,
                "memory_vms_bytes": self.memory_vms_bytes,
                "memory_percent": self.memory_percent,
            },
            "disk": {
                "home_dir_bytes": self.home_dir_bytes,
                "log_dir_bytes": self.log_dir_bytes,
                "backup_dir_bytes": self.backup_dir_bytes,
                "total_bytes": self.total_disk_bytes,
            },
            "database": {
                "name": self.database_name,
                "size_bytes": self.database_size_bytes,
                "connections": self.database_connections,
            }
            if self.database_name
            else None,
            "alerts": self.alerts,
        }


class ResourceServiceError(Exception):
    """Exception for resource service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Default thresholds
DEFAULT_THRESHOLDS = {
    "memory_warning_percent": 80.0,
    "memory_critical_percent": 95.0,
    "disk_warning_percent": 85.0,
    "disk_critical_percent": 95.0,
    "cpu_warning_percent": 80.0,
    "cpu_critical_percent": 95.0,
}


class ResourceService:
    """Service for monitoring project resource usage."""

    def __init__(self) -> None:
        self.db = get_db()

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists and return project info."""
        project_info = self.db.get_project(project)
        if not project_info:
            raise ResourceServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return project_info

    def _get_service_name(self, project: str) -> str:
        """Get the systemd service name for a project."""
        return f"hostkit-{project}"

    def _get_process_metrics(self, service_name: str) -> dict[str, Any]:
        """Get process metrics for a systemd service.

        Returns dict with:
            - running: bool
            - pid: int | None
            - cpu_percent: float | None
            - memory_rss_bytes: int | None
            - memory_vms_bytes: int | None
            - memory_percent: float | None
            - process_count: int
        """
        result: dict[str, Any] = {
            "running": False,
            "pid": None,
            "cpu_percent": None,
            "memory_rss_bytes": None,
            "memory_vms_bytes": None,
            "memory_percent": None,
            "process_count": 0,
        }

        try:
            # Check service status
            status_result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
            )
            result["running"] = status_result.returncode == 0

            if not result["running"]:
                return result

            # Get main PID
            pid_result = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", service_name],
                capture_output=True,
                text=True,
            )
            if pid_result.returncode == 0:
                pid_str = pid_result.stdout.strip().replace("MainPID=", "")
                if pid_str and pid_str != "0":
                    result["pid"] = int(pid_str)

            # Get process metrics including child processes
            if result["pid"]:
                try:
                    main_proc = psutil.Process(result["pid"])

                    # Get all child processes
                    children = main_proc.children(recursive=True)
                    all_procs = [main_proc] + children
                    result["process_count"] = len(all_procs)

                    # Aggregate metrics across all processes
                    total_rss = 0
                    total_vms = 0
                    total_cpu = 0.0

                    for proc in all_procs:
                        try:
                            mem_info = proc.memory_info()
                            total_rss += mem_info.rss
                            total_vms += mem_info.vms
                            total_cpu += proc.cpu_percent(interval=0.1)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

                    result["memory_rss_bytes"] = total_rss
                    result["memory_vms_bytes"] = total_vms
                    result["cpu_percent"] = round(total_cpu, 2)

                    # Calculate memory percent
                    total_memory = psutil.virtual_memory().total
                    if total_memory > 0:
                        result["memory_percent"] = round((total_rss / total_memory) * 100, 2)

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        except (subprocess.SubprocessError, ValueError):
            pass

        return result

    def _get_directory_size(self, path: Path) -> int:
        """Get total size of a directory in bytes."""
        if not path.exists():
            return 0

        total_size = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    try:
                        total_size += entry.stat().st_size
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass

        return total_size

    def _get_disk_usage(self, project: str) -> dict[str, int]:
        """Get disk usage for project directories.

        Returns dict with:
            - home_dir_bytes: int
            - log_dir_bytes: int
            - backup_dir_bytes: int
            - total_bytes: int
        """
        home_path = Path(f"/home/{project}")
        log_path = Path(f"/var/log/projects/{project}")
        backup_path = Path(f"/backups/{project}")

        home_size = self._get_directory_size(home_path)
        log_size = self._get_directory_size(log_path)
        backup_size = self._get_directory_size(backup_path)

        return {
            "home_dir_bytes": home_size,
            "log_dir_bytes": log_size,
            "backup_dir_bytes": backup_size,
            "total_bytes": home_size + log_size + backup_size,
        }

    def _get_database_metrics(self, project: str) -> dict[str, Any] | None:
        """Get database metrics for a project.

        Returns dict with:
            - name: str
            - size_bytes: int
            - connections: int
        Or None if project has no database.
        """
        # Check if project has a database by looking for DATABASE_URL in .env
        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            return None

        database_url = None
        db_name = None
        try:
            content = env_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    # Extract database name from URL
                    # Format: postgresql://user:pass@host:port/dbname
                    if "/" in database_url:
                        db_name = database_url.rsplit("/", 1)[-1].split("?")[0]
                    break
        except OSError:
            return None

        if not database_url or not db_name:
            return None

        result: dict[str, Any] = {
            "name": db_name,
            "size_bytes": None,
            "connections": None,
        }

        # Get database size
        try:
            size_result = subprocess.run(
                [
                    "sudo",
                    "-u",
                    "postgres",
                    "psql",
                    "-t",
                    "-c",
                    f"SELECT pg_database_size('{db_name}')",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if size_result.returncode == 0:
                size_str = size_result.stdout.strip()
                if size_str.isdigit():
                    result["size_bytes"] = int(size_str)
        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass

        # Get active connections count
        try:
            conn_result = subprocess.run(
                [
                    "sudo",
                    "-u",
                    "postgres",
                    "psql",
                    "-t",
                    "-c",
                    f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{db_name}'",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if conn_result.returncode == 0:
                conn_str = conn_result.stdout.strip()
                if conn_str.isdigit():
                    result["connections"] = int(conn_str)
        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass

        return result

    def _check_thresholds(
        self, resources: ProjectResources, thresholds: dict[str, float] | None = None
    ) -> list[dict[str, Any]]:
        """Check resource metrics against thresholds.

        Returns list of alerts with:
            - level: "warning" | "critical"
            - metric: str
            - value: float
            - threshold: float
            - message: str
        """
        if thresholds is None:
            thresholds = DEFAULT_THRESHOLDS

        alerts = []

        # Check memory
        if resources.memory_percent is not None:
            if resources.memory_percent >= thresholds["memory_critical_percent"]:
                alerts.append(
                    {
                        "level": "critical",
                        "metric": "memory_percent",
                        "value": resources.memory_percent,
                        "threshold": thresholds["memory_critical_percent"],
                        "message": f"Memory usage critical: {resources.memory_percent:.1f}%",
                    }
                )
            elif resources.memory_percent >= thresholds["memory_warning_percent"]:
                alerts.append(
                    {
                        "level": "warning",
                        "metric": "memory_percent",
                        "value": resources.memory_percent,
                        "threshold": thresholds["memory_warning_percent"],
                        "message": f"Memory usage high: {resources.memory_percent:.1f}%",
                    }
                )

        # Check CPU
        if resources.cpu_percent is not None:
            if resources.cpu_percent >= thresholds["cpu_critical_percent"]:
                alerts.append(
                    {
                        "level": "critical",
                        "metric": "cpu_percent",
                        "value": resources.cpu_percent,
                        "threshold": thresholds["cpu_critical_percent"],
                        "message": f"CPU usage critical: {resources.cpu_percent:.1f}%",
                    }
                )
            elif resources.cpu_percent >= thresholds["cpu_warning_percent"]:
                alerts.append(
                    {
                        "level": "warning",
                        "metric": "cpu_percent",
                        "value": resources.cpu_percent,
                        "threshold": thresholds["cpu_warning_percent"],
                        "message": f"CPU usage high: {resources.cpu_percent:.1f}%",
                    }
                )

        # Check disk (use system disk usage for the project's home directory)
        try:
            home_path = Path(f"/home/{resources.project}")
            if home_path.exists():
                disk_usage = psutil.disk_usage(str(home_path))
                disk_percent = disk_usage.percent
                if disk_percent >= thresholds["disk_critical_percent"]:
                    alerts.append(
                        {
                            "level": "critical",
                            "metric": "disk_percent",
                            "value": disk_percent,
                            "threshold": thresholds["disk_critical_percent"],
                            "message": f"Disk usage critical: {disk_percent:.1f}%",
                        }
                    )
                elif disk_percent >= thresholds["disk_warning_percent"]:
                    alerts.append(
                        {
                            "level": "warning",
                            "metric": "disk_percent",
                            "value": disk_percent,
                            "threshold": thresholds["disk_warning_percent"],
                            "message": f"Disk usage high: {disk_percent:.1f}%",
                        }
                    )
        except (OSError, PermissionError):
            pass

        return alerts

    def get_project_resources(
        self, project: str, thresholds: dict[str, float] | None = None
    ) -> ProjectResources:
        """Get comprehensive resource metrics for a project.

        Args:
            project: Project name
            thresholds: Optional custom thresholds for alerts

        Returns:
            ProjectResources with all metrics and alerts
        """
        self._validate_project(project)

        service_name = self._get_service_name(project)
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Get process metrics
        process_metrics = self._get_process_metrics(service_name)

        # Get disk usage
        disk_usage = self._get_disk_usage(project)

        # Get database metrics
        db_metrics = self._get_database_metrics(project)

        # Build resources object
        resources = ProjectResources(
            project=project,
            timestamp=timestamp,
            process_count=process_metrics["process_count"],
            main_pid=process_metrics["pid"],
            cpu_percent=process_metrics["cpu_percent"],
            memory_rss_bytes=process_metrics["memory_rss_bytes"],
            memory_vms_bytes=process_metrics["memory_vms_bytes"],
            memory_percent=process_metrics["memory_percent"],
            home_dir_bytes=disk_usage["home_dir_bytes"],
            log_dir_bytes=disk_usage["log_dir_bytes"],
            backup_dir_bytes=disk_usage["backup_dir_bytes"],
            total_disk_bytes=disk_usage["total_bytes"],
            database_name=db_metrics["name"] if db_metrics else None,
            database_size_bytes=db_metrics["size_bytes"] if db_metrics else None,
            database_connections=db_metrics["connections"] if db_metrics else None,
        )

        # Check thresholds and add alerts
        resources.alerts = self._check_thresholds(resources, thresholds)

        return resources

    def watch_resources(
        self,
        project: str,
        interval: int = 30,
        thresholds: dict[str, float] | None = None,
    ) -> Generator[ProjectResources, None, None]:
        """Continuously monitor project resources.

        Args:
            project: Project name
            interval: Seconds between checks
            thresholds: Optional custom thresholds for alerts

        Yields:
            ProjectResources at each interval
        """
        # Validate project once at start
        self._validate_project(project)

        while True:
            yield self.get_project_resources(project, thresholds)
            time.sleep(interval)

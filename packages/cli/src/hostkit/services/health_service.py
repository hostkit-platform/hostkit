"""Health check service for HostKit projects."""

import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import requests

from hostkit.database import get_db


@dataclass
class HealthCheck:
    """Result of a health check for a project."""

    project: str
    overall: str  # "healthy", "degraded", "unhealthy"
    http_status: int | None = None
    http_response_ms: float | None = None
    http_body: str | None = None
    process_running: bool = False
    process_memory_mb: float | None = None
    process_cpu_percent: float | None = None
    database_connected: bool | None = None
    auth_service_running: bool | None = None
    checks: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "overall": self.overall,
            "http_status": self.http_status,
            "http_response_ms": self.http_response_ms,
            "http_body": self.http_body,
            "process_running": self.process_running,
            "process_memory_mb": self.process_memory_mb,
            "process_cpu_percent": self.process_cpu_percent,
            "database_connected": self.database_connected,
            "auth_service_running": self.auth_service_running,
            "checks": self.checks,
            "error": self.error,
        }


class HealthServiceError(Exception):
    """Exception for health service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class HealthService:
    """Service for checking project health."""

    def __init__(self) -> None:
        self.db = get_db()

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists and return project info."""
        project_info = self.db.get_project(project)
        if not project_info:
            raise HealthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return project_info

    def _get_service_name(self, project: str) -> str:
        """Get the systemd service name for a project."""
        return f"hostkit-{project}"

    def _get_auth_service_name(self, project: str) -> str:
        """Get the auth service name for a project."""
        return f"hostkit-{project}-auth"

    def _check_process_status(self, service_name: str) -> dict[str, Any]:
        """Check if a systemd service is running and get its process info.

        Returns dict with:
            - running: bool
            - pid: int | None
            - memory_mb: float | None
            - cpu_percent: float | None
        """
        result: dict[str, Any] = {
            "running": False,
            "pid": None,
            "memory_mb": None,
            "cpu_percent": None,
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

            # Get process metrics
            if result["pid"]:
                try:
                    proc = psutil.Process(result["pid"])
                    result["memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 2)
                    result["cpu_percent"] = proc.cpu_percent(interval=0.1)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        except (subprocess.SubprocessError, ValueError):
            pass

        return result

    def _check_http_health(
        self,
        project: str,
        port: int,
        endpoint: str = "/health",
        timeout: int = 10,
        expected_content: str | None = None,
    ) -> dict[str, Any]:
        """Check HTTP health endpoint.

        Tries multiple endpoints in order to find a working health check:
        1. The specified endpoint (default /health)
        2. /api/health (common for API servers)
        3. / (root, any response means service is running)

        Returns dict with:
            - status: int | None
            - response_ms: float | None
            - body: str | None
            - error: str | None
            - content_match: bool | None (if expected_content provided)
            - endpoint_used: str | None (which endpoint succeeded)
            - service_responding: bool (true if any endpoint responded)
        """
        result: dict[str, Any] = {
            "status": None,
            "response_ms": None,
            "body": None,
            "error": None,
            "content_match": None,
            "endpoint_used": None,
            "service_responding": False,
        }

        # Try health endpoints in order of preference
        endpoints_to_try = [endpoint]
        if endpoint != "/api/health":
            endpoints_to_try.append("/api/health")
        if endpoint != "/":
            endpoints_to_try.append("/")

        for try_endpoint in endpoints_to_try:
            url = f"http://127.0.0.1:{port}{try_endpoint}"

            try:
                start_time = time.time()
                response = requests.get(url, timeout=timeout)
                elapsed_ms = (time.time() - start_time) * 1000

                # Any response means the service is responding
                result["service_responding"] = True
                result["status"] = response.status_code
                result["response_ms"] = round(elapsed_ms, 2)
                result["body"] = response.text[:500]
                result["endpoint_used"] = try_endpoint

                if expected_content:
                    result["content_match"] = expected_content in response.text

                # If we got a 200, use this result
                if response.status_code == 200:
                    return result

                # If we got a response (even 404), service is running
                # Keep trying other endpoints for a 200, but note it's responding

            except requests.exceptions.ConnectionError:
                if result["error"] is None:
                    result["error"] = "Connection refused"
            except requests.exceptions.Timeout:
                if result["error"] is None:
                    result["error"] = f"Timeout after {timeout}s"
            except requests.exceptions.RequestException as e:
                if result["error"] is None:
                    result["error"] = str(e)

        # If service responded but no 200, clear the error - service is up
        if result["service_responding"]:
            result["error"] = None

        return result

    def _check_database_connection(self, project: str) -> bool | None:
        """Check if project can connect to its database.

        Reads DATABASE_URL from .env and tries to connect.
        Returns None if no database configured.
        """
        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            return None

        # Parse .env for DATABASE_URL
        database_url = None
        try:
            content = env_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError:
            return None

        if not database_url:
            return None

        # Try to connect using psql
        try:
            result = subprocess.run(
                ["psql", database_url, "-c", "SELECT 1"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            return False

    def check_health(
        self,
        project: str,
        endpoint: str = "/health",
        timeout: int = 10,
        expected_content: str | None = None,
    ) -> HealthCheck:
        """Perform a comprehensive health check on a project.

        Args:
            project: Project name
            endpoint: HTTP endpoint to check (default: /health)
            timeout: HTTP timeout in seconds
            expected_content: Expected content in response body

        Returns:
            HealthCheck with all check results
        """
        project_info = self._validate_project(project)
        port = project_info["port"]
        service_name = self._get_service_name(project)

        checks: dict[str, Any] = {}

        # Check process status
        process_info = self._check_process_status(service_name)
        checks["process"] = process_info

        # Check HTTP health
        http_info = self._check_http_health(project, port, endpoint, timeout, expected_content)
        checks["http"] = http_info

        # Check database connectivity
        db_connected = self._check_database_connection(project)
        checks["database"] = {"connected": db_connected}

        # Check auth service if enabled
        auth_service = self.db.get_auth_service(project)
        auth_running = None
        if auth_service and auth_service.get("enabled"):
            auth_service_name = self._get_auth_service_name(project)
            auth_info = self._check_process_status(auth_service_name)
            auth_running = auth_info["running"]
            checks["auth_service"] = auth_info

        # Determine overall status
        overall = self._determine_overall_status(
            process_running=process_info["running"],
            http_status=http_info["status"],
            http_error=http_info["error"],
            db_connected=db_connected,
            auth_running=auth_running,
            auth_enabled=bool(auth_service and auth_service.get("enabled")),
            content_match=http_info.get("content_match"),
            expected_content=expected_content,
            service_responding=http_info.get("service_responding", False),
        )

        return HealthCheck(
            project=project,
            overall=overall,
            http_status=http_info["status"],
            http_response_ms=http_info["response_ms"],
            http_body=http_info["body"],
            process_running=process_info["running"],
            process_memory_mb=process_info["memory_mb"],
            process_cpu_percent=process_info["cpu_percent"],
            database_connected=db_connected,
            auth_service_running=auth_running,
            checks=checks,
            error=http_info["error"],
        )

    def _determine_overall_status(
        self,
        process_running: bool,
        http_status: int | None,
        http_error: str | None,
        db_connected: bool | None,
        auth_running: bool | None,
        auth_enabled: bool,
        content_match: bool | None,
        expected_content: str | None,
        service_responding: bool = False,
    ) -> str:
        """Determine overall health status.

        Returns:
            "healthy" - All checks pass
            "degraded" - Some non-critical checks fail
            "unhealthy" - Critical checks fail
        """
        # Unhealthy if process not running
        if not process_running:
            return "unhealthy"

        # Unhealthy if HTTP check completely fails (connection refused, timeout)
        # But NOT if service is responding (even with 4xx/5xx)
        if http_error and not service_responding:
            return "unhealthy"

        # 5xx errors are unhealthy
        if http_status and http_status >= 500:
            return "unhealthy"

        # Unhealthy if expected content not found
        if expected_content and content_match is False:
            return "unhealthy"

        # Degraded conditions
        is_degraded = False

        # HTTP 4xx is only degraded if we're NOT just responding to a missing health endpoint
        # If service_responding is True and status is 404, it's probably just no /health route
        # which is fine - the service is running
        if http_status and 400 <= http_status < 500:
            # 404 on health endpoint when service is responding is OK
            if not (http_status == 404 and service_responding):
                is_degraded = True

        # Database connection failed is degraded
        if db_connected is False:
            is_degraded = True

        # Auth service down when enabled is degraded
        if auth_enabled and auth_running is False:
            is_degraded = True

        if is_degraded:
            return "degraded"

        return "healthy"

    def watch_health(
        self,
        project: str,
        endpoint: str = "/health",
        interval: int = 30,
        timeout: int = 10,
        expected_content: str | None = None,
    ) -> Generator[HealthCheck, None, None]:
        """Continuously monitor project health.

        Args:
            project: Project name
            endpoint: HTTP endpoint to check
            interval: Seconds between checks
            timeout: HTTP timeout in seconds
            expected_content: Expected content in response body

        Yields:
            HealthCheck results at each interval
        """
        # Validate project once at start
        self._validate_project(project)

        while True:
            yield self.check_health(
                project,
                endpoint=endpoint,
                timeout=timeout,
                expected_content=expected_content,
            )
            time.sleep(interval)

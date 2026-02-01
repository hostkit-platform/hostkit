"""Metrics collection and querying service for HostKit projects."""

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from hostkit.database import get_db


@dataclass
class MetricsSample:
    """A single metrics sample for a project."""

    project: str
    collected_at: str
    metric_type: str  # 'system', 'application', 'database'
    # System metrics
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None
    memory_percent: float | None = None
    disk_used_bytes: int | None = None
    process_count: int | None = None
    # Application metrics (from Nginx)
    requests_total: int | None = None
    requests_2xx: int | None = None
    requests_4xx: int | None = None
    requests_5xx: int | None = None
    avg_response_ms: float | None = None
    p95_response_ms: float | None = None
    # Database metrics
    db_size_bytes: int | None = None
    db_connections: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "collected_at": self.collected_at,
            "metric_type": self.metric_type,
            "system": {
                "cpu_percent": self.cpu_percent,
                "memory_rss_bytes": self.memory_rss_bytes,
                "memory_percent": self.memory_percent,
                "disk_used_bytes": self.disk_used_bytes,
                "process_count": self.process_count,
            },
            "application": {
                "requests_total": self.requests_total,
                "requests_2xx": self.requests_2xx,
                "requests_4xx": self.requests_4xx,
                "requests_5xx": self.requests_5xx,
                "avg_response_ms": self.avg_response_ms,
                "p95_response_ms": self.p95_response_ms,
            },
            "database": {
                "size_bytes": self.db_size_bytes,
                "connections": self.db_connections,
            },
        }


@dataclass
class MetricsConfig:
    """Configuration for metrics collection for a project."""

    project_name: str
    enabled: bool = False
    collection_interval: int = 60
    retention_days: int = 7
    alert_on_threshold: bool = True
    cpu_warning_percent: float | None = None
    cpu_critical_percent: float | None = None
    memory_warning_percent: float | None = None
    memory_critical_percent: float | None = None
    error_rate_warning_percent: float | None = None
    error_rate_critical_percent: float | None = None
    last_collected_at: str | None = None
    nginx_log_position: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MetricsSummary:
    """Summary statistics for metrics over a time period."""

    project: str
    period_start: str
    period_end: str
    sample_count: int
    # System metrics summary
    cpu_avg: float | None = None
    cpu_max: float | None = None
    memory_avg: float | None = None
    memory_max: float | None = None
    # Application metrics summary
    total_requests: int | None = None
    total_2xx: int | None = None
    total_4xx: int | None = None
    total_5xx: int | None = None
    error_rate: float | None = None
    avg_response_ms: float | None = None
    p95_response_ms: float | None = None
    # Database metrics summary
    db_size_latest: int | None = None
    db_connections_avg: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "sample_count": self.sample_count,
            "system": {
                "cpu_avg": self.cpu_avg,
                "cpu_max": self.cpu_max,
                "memory_avg": self.memory_avg,
                "memory_max": self.memory_max,
            },
            "application": {
                "total_requests": self.total_requests,
                "total_2xx": self.total_2xx,
                "total_4xx": self.total_4xx,
                "total_5xx": self.total_5xx,
                "error_rate": self.error_rate,
                "avg_response_ms": self.avg_response_ms,
                "p95_response_ms": self.p95_response_ms,
            },
            "database": {
                "size_bytes": self.db_size_latest,
                "connections_avg": self.db_connections_avg,
            },
        }


@dataclass
class ThresholdAlert:
    """Alert triggered by a metric threshold."""

    project: str
    metric: str
    level: str  # 'warning' or 'critical'
    value: float
    threshold: float
    message: str
    timestamp: str


class MetricsServiceError(Exception):
    """Exception for metrics service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Default thresholds
DEFAULT_THRESHOLDS = {
    "cpu_warning_percent": 80.0,
    "cpu_critical_percent": 95.0,
    "memory_warning_percent": 80.0,
    "memory_critical_percent": 95.0,
    "error_rate_warning_percent": 5.0,
    "error_rate_critical_percent": 10.0,
}


class MetricsService:
    """Service for collecting, storing, and querying project metrics."""

    def __init__(self) -> None:
        self.db = get_db()

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists and return project info."""
        project_info = self.db.get_project(project)
        if not project_info:
            raise MetricsServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return project_info

    def _get_service_name(self, project: str) -> str:
        """Get the systemd service name for a project."""
        return f"hostkit-{project}"

    # -------------------------------------------------------------------------
    # Configuration Management
    # -------------------------------------------------------------------------

    def get_config(self, project: str) -> MetricsConfig:
        """Get metrics configuration for a project."""
        self._validate_project(project)

        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM metrics_config WHERE project_name = ?",
                (project,),
            )
            row = cursor.fetchone()

            if row:
                return MetricsConfig(
                    project_name=row["project_name"],
                    enabled=bool(row["enabled"]),
                    collection_interval=row["collection_interval"],
                    retention_days=row["retention_days"],
                    alert_on_threshold=bool(row["alert_on_threshold"]),
                    cpu_warning_percent=row["cpu_warning_percent"],
                    cpu_critical_percent=row["cpu_critical_percent"],
                    memory_warning_percent=row["memory_warning_percent"],
                    memory_critical_percent=row["memory_critical_percent"],
                    error_rate_warning_percent=row["error_rate_warning_percent"],
                    error_rate_critical_percent=row["error_rate_critical_percent"],
                    last_collected_at=row["last_collected_at"],
                    nginx_log_position=row["nginx_log_position"] or 0,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

            # Return defaults if no config exists
            return MetricsConfig(project_name=project)

    def enable_metrics(self, project: str) -> MetricsConfig:
        """Enable metrics collection for a project."""
        self._validate_project(project)
        now = datetime.utcnow().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO metrics_config (
                    project_name, enabled, collection_interval, retention_days,
                    alert_on_threshold, created_at, updated_at
                )
                VALUES (?, 1, 60, 7, 1, ?, ?)
                ON CONFLICT(project_name) DO UPDATE SET
                    enabled = 1,
                    updated_at = ?
                """,
                (project, now, now, now),
            )

        return self.get_config(project)

    def disable_metrics(self, project: str) -> MetricsConfig:
        """Disable metrics collection for a project."""
        self._validate_project(project)
        now = datetime.utcnow().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE metrics_config SET enabled = 0, updated_at = ?
                WHERE project_name = ?
                """,
                (now, project),
            )

        return self.get_config(project)

    def update_config(
        self,
        project: str,
        collection_interval: int | None = None,
        retention_days: int | None = None,
        alert_on_threshold: bool | None = None,
        cpu_warning: float | None = None,
        cpu_critical: float | None = None,
        memory_warning: float | None = None,
        memory_critical: float | None = None,
        error_rate_warning: float | None = None,
        error_rate_critical: float | None = None,
    ) -> MetricsConfig:
        """Update metrics configuration for a project."""
        self._validate_project(project)
        now = datetime.utcnow().isoformat()

        # Ensure config exists
        config = self.get_config(project)
        if not config.created_at:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO metrics_config (project_name, enabled, created_at, updated_at)
                    VALUES (?, 0, ?, ?)
                    """,
                    (project, now, now),
                )

        # Build update query
        updates = []
        params = []

        if collection_interval is not None:
            updates.append("collection_interval = ?")
            params.append(collection_interval)
        if retention_days is not None:
            updates.append("retention_days = ?")
            params.append(retention_days)
        if alert_on_threshold is not None:
            updates.append("alert_on_threshold = ?")
            params.append(1 if alert_on_threshold else 0)
        if cpu_warning is not None:
            updates.append("cpu_warning_percent = ?")
            params.append(cpu_warning)
        if cpu_critical is not None:
            updates.append("cpu_critical_percent = ?")
            params.append(cpu_critical)
        if memory_warning is not None:
            updates.append("memory_warning_percent = ?")
            params.append(memory_warning)
        if memory_critical is not None:
            updates.append("memory_critical_percent = ?")
            params.append(memory_critical)
        if error_rate_warning is not None:
            updates.append("error_rate_warning_percent = ?")
            params.append(error_rate_warning)
        if error_rate_critical is not None:
            updates.append("error_rate_critical_percent = ?")
            params.append(error_rate_critical)

        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(project)

            with self.db.transaction() as conn:
                conn.execute(
                    f"UPDATE metrics_config SET {', '.join(updates)} WHERE project_name = ?",
                    params,
                )

        return self.get_config(project)

    # -------------------------------------------------------------------------
    # Metrics Collection
    # -------------------------------------------------------------------------

    def _collect_system_metrics(self, project: str) -> dict[str, Any]:
        """Collect system metrics for a project."""
        service_name = self._get_service_name(project)
        result = {
            "cpu_percent": None,
            "memory_rss_bytes": None,
            "memory_percent": None,
            "disk_used_bytes": None,
            "process_count": 0,
        }

        # Check service status
        try:
            status_result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
            )
            if status_result.returncode != 0:
                return result

            # Get main PID
            pid_result = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", service_name],
                capture_output=True,
                text=True,
            )
            if pid_result.returncode != 0:
                return result

            pid_str = pid_result.stdout.strip().replace("MainPID=", "")
            if not pid_str or pid_str == "0":
                return result

            pid = int(pid_str)

            # Get process metrics including children
            main_proc = psutil.Process(pid)
            children = main_proc.children(recursive=True)
            all_procs = [main_proc] + children
            result["process_count"] = len(all_procs)

            total_rss = 0
            total_cpu = 0.0

            for proc in all_procs:
                try:
                    mem_info = proc.memory_info()
                    total_rss += mem_info.rss
                    total_cpu += proc.cpu_percent(interval=0.1)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            result["memory_rss_bytes"] = total_rss
            result["cpu_percent"] = round(total_cpu, 2)

            # Calculate memory percent
            total_memory = psutil.virtual_memory().total
            if total_memory > 0:
                result["memory_percent"] = round((total_rss / total_memory) * 100, 2)

        except (subprocess.SubprocessError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        # Get disk usage
        home_path = Path(f"/home/{project}")
        if home_path.exists():
            try:
                total_size = 0
                for entry in home_path.rglob("*"):
                    if entry.is_file():
                        try:
                            total_size += entry.stat().st_size
                        except (OSError, PermissionError):
                            continue
                result["disk_used_bytes"] = total_size
            except (OSError, PermissionError):
                pass

        return result

    def _collect_database_metrics(self, project: str) -> dict[str, Any] | None:
        """Collect database metrics for a project."""
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
                    if "/" in database_url:
                        db_name = database_url.rsplit("/", 1)[-1].split("?")[0]
                    break
        except OSError:
            return None

        if not database_url or not db_name:
            return None

        result = {"size_bytes": None, "connections": None}

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

        # Get active connections
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

    def _parse_nginx_logs(self, project: str, config: MetricsConfig) -> dict[str, Any]:
        """Parse Nginx access logs for application metrics.

        Expected log format with $request_time at the end:
        $remote_addr - $remote_user [$time_local] "$request"
        $status $body_bytes_sent "$http_referer"
        "$http_user_agent" $request_time

        Returns dict with:
            - requests_total: int
            - requests_2xx: int
            - requests_4xx: int
            - requests_5xx: int
            - avg_response_ms: float
            - p95_response_ms: float
            - new_position: int (file position for next read)
        """
        result = {
            "requests_total": 0,
            "requests_2xx": 0,
            "requests_4xx": 0,
            "requests_5xx": 0,
            "avg_response_ms": None,
            "p95_response_ms": None,
            "new_position": config.nginx_log_position,
        }

        # Find Nginx access log for this project
        log_paths = [
            Path(f"/var/log/nginx/{project}.access.log"),
        ]
        # Also check for nip.io dev domain logs (any IP)
        nginx_log_dir = Path("/var/log/nginx")
        if nginx_log_dir.exists():
            log_paths.extend(nginx_log_dir.glob(f"{project}.*.nip.io.access.log"))

        log_path = None
        for p in log_paths:
            if p.exists():
                log_path = p
                break

        if not log_path:
            return result

        # Regex to parse Nginx combined log format with request_time
        # Example: 127.0.0.1 - - [15/Dec/2025:10:30:00 +0000]
        # "GET / HTTP/1.1" 200 1234 "-" "curl" 0.005
        log_pattern = re.compile(
            r"^(\S+)\s+"  # remote_addr
            r"\S+\s+"  # remote_user (usually -)
            r"\S+\s+"  # remote_user (usually -)
            r"\[([^\]]+)\]\s+"  # time_local
            r'"([^"]*)"\s+'  # request
            r"(\d+)\s+"  # status
            r"(\d+)\s+"  # body_bytes_sent
            r'"([^"]*)"\s+'  # referer
            r'"([^"]*)"\s*'  # user_agent
            r"(\d+\.?\d*)?"  # request_time (optional)
        )

        response_times = []
        status_counts = {"2xx": 0, "4xx": 0, "5xx": 0}

        try:
            with open(log_path) as f:
                # Seek to last position
                f.seek(config.nginx_log_position)

                for line in f:
                    match = log_pattern.match(line)
                    if match:
                        status = int(match.group(4))
                        request_time = match.group(8)

                        # Count by status code
                        result["requests_total"] += 1
                        if 200 <= status < 300:
                            status_counts["2xx"] += 1
                        elif 400 <= status < 500:
                            status_counts["4xx"] += 1
                        elif 500 <= status < 600:
                            status_counts["5xx"] += 1

                        # Collect response time (in seconds, convert to ms)
                        if request_time:
                            try:
                                response_times.append(float(request_time) * 1000)
                            except ValueError:
                                pass

                # Save new position
                result["new_position"] = f.tell()

        except (OSError, PermissionError):
            return result

        result["requests_2xx"] = status_counts["2xx"]
        result["requests_4xx"] = status_counts["4xx"]
        result["requests_5xx"] = status_counts["5xx"]

        # Calculate response time statistics
        if response_times:
            result["avg_response_ms"] = round(sum(response_times) / len(response_times), 2)
            sorted_times = sorted(response_times)
            p95_idx = int(len(sorted_times) * 0.95)
            result["p95_response_ms"] = round(
                sorted_times[p95_idx] if p95_idx < len(sorted_times) else sorted_times[-1], 2
            )

        return result

    def collect_metrics(self, project: str, send_alerts: bool = True) -> MetricsSample:
        """Collect all metrics for a project and store in database.

        Args:
            project: Project name
            send_alerts: Whether to send alerts for threshold violations

        Returns:
            The collected MetricsSample
        """
        self._validate_project(project)
        config = self.get_config(project)
        now = datetime.utcnow().isoformat()

        # Collect system metrics
        system = self._collect_system_metrics(project)

        # Collect database metrics
        db_metrics = self._collect_database_metrics(project)

        # Collect application metrics from Nginx
        app_metrics = self._parse_nginx_logs(project, config)

        # Create sample
        sample = MetricsSample(
            project=project,
            collected_at=now,
            metric_type="combined",
            cpu_percent=system["cpu_percent"],
            memory_rss_bytes=system["memory_rss_bytes"],
            memory_percent=system["memory_percent"],
            disk_used_bytes=system["disk_used_bytes"],
            process_count=system["process_count"],
            requests_total=app_metrics["requests_total"],
            requests_2xx=app_metrics["requests_2xx"],
            requests_4xx=app_metrics["requests_4xx"],
            requests_5xx=app_metrics["requests_5xx"],
            avg_response_ms=app_metrics["avg_response_ms"],
            p95_response_ms=app_metrics["p95_response_ms"],
            db_size_bytes=db_metrics["size_bytes"] if db_metrics else None,
            db_connections=db_metrics["connections"] if db_metrics else None,
        )

        # Store in database
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO metrics (
                    project_name, collected_at, metric_type,
                    cpu_percent, memory_rss_bytes, memory_percent,
                    disk_used_bytes, process_count,
                    requests_total, requests_2xx, requests_4xx, requests_5xx,
                    avg_response_ms, p95_response_ms,
                    db_size_bytes, db_connections
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project,
                    now,
                    "combined",
                    sample.cpu_percent,
                    sample.memory_rss_bytes,
                    sample.memory_percent,
                    sample.disk_used_bytes,
                    sample.process_count,
                    sample.requests_total,
                    sample.requests_2xx,
                    sample.requests_4xx,
                    sample.requests_5xx,
                    sample.avg_response_ms,
                    sample.p95_response_ms,
                    sample.db_size_bytes,
                    sample.db_connections,
                ),
            )

            # Update config with last collection time and nginx position
            conn.execute(
                """
                UPDATE metrics_config
                SET last_collected_at = ?, nginx_log_position = ?, updated_at = ?
                WHERE project_name = ?
                """,
                (now, app_metrics["new_position"], now, project),
            )

        # Check thresholds and send alerts if enabled
        if send_alerts and config.alert_on_threshold:
            alerts = self.check_thresholds(sample, config)
            if alerts:
                self._send_threshold_alerts(project, alerts)

        return sample

    def _send_threshold_alerts(self, project: str, alerts: list[ThresholdAlert]) -> None:
        """Send alerts for threshold violations.

        Only sends alerts for critical violations to avoid spam.
        """
        try:
            from hostkit.services.alert_service import send_alert

            # Only send alerts for critical level
            critical_alerts = [a for a in alerts if a.level == "critical"]
            if not critical_alerts:
                return

            # Build alert message
            messages = [a.message for a in critical_alerts]
            metrics_data = {a.metric: a.value for a in critical_alerts}

            send_alert(
                project_name=project,
                event_type="metrics",
                event_status="failure",
                data={
                    "alerts": [
                        {
                            "metric": a.metric,
                            "level": a.level,
                            "value": a.value,
                            "threshold": a.threshold,
                            "message": a.message,
                        }
                        for a in critical_alerts
                    ],
                    "messages": messages,
                    "metrics": metrics_data,
                },
            )
        except Exception:
            # Don't fail metrics collection if alerting fails
            pass

    def collect_all_metrics(self) -> list[MetricsSample]:
        """Collect metrics for all projects with metrics enabled."""
        samples = []

        with self.db.connection() as conn:
            cursor = conn.execute("SELECT project_name FROM metrics_config WHERE enabled = 1")
            projects = [row["project_name"] for row in cursor.fetchall()]

        for project in projects:
            try:
                sample = self.collect_metrics(project)
                samples.append(sample)
            except MetricsServiceError:
                # Skip projects that no longer exist
                continue

        return samples

    # -------------------------------------------------------------------------
    # Querying Metrics
    # -------------------------------------------------------------------------

    def get_latest(self, project: str) -> MetricsSample | None:
        """Get the most recent metrics sample for a project."""
        self._validate_project(project)

        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM metrics
                WHERE project_name = ?
                ORDER BY collected_at DESC
                LIMIT 1
                """,
                (project,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            return MetricsSample(
                project=row["project_name"],
                collected_at=row["collected_at"],
                metric_type=row["metric_type"],
                cpu_percent=row["cpu_percent"],
                memory_rss_bytes=row["memory_rss_bytes"],
                memory_percent=row["memory_percent"],
                disk_used_bytes=row["disk_used_bytes"],
                process_count=row["process_count"],
                requests_total=row["requests_total"],
                requests_2xx=row["requests_2xx"],
                requests_4xx=row["requests_4xx"],
                requests_5xx=row["requests_5xx"],
                avg_response_ms=row["avg_response_ms"],
                p95_response_ms=row["p95_response_ms"],
                db_size_bytes=row["db_size_bytes"],
                db_connections=row["db_connections"],
            )

    def get_history(
        self,
        project: str,
        since: str | None = None,
        limit: int = 100,
    ) -> list[MetricsSample]:
        """Get historical metrics for a project.

        Args:
            project: Project name
            since: ISO timestamp or duration (e.g., "1h", "24h", "7d")
            limit: Maximum number of samples to return
        """
        self._validate_project(project)

        # Parse since parameter
        start_time = None
        if since:
            start_time = self._parse_since(since)

        with self.db.connection() as conn:
            if start_time:
                cursor = conn.execute(
                    """
                    SELECT * FROM metrics
                    WHERE project_name = ? AND collected_at >= ?
                    ORDER BY collected_at DESC
                    LIMIT ?
                    """,
                    (project, start_time.isoformat(), limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM metrics
                    WHERE project_name = ?
                    ORDER BY collected_at DESC
                    LIMIT ?
                    """,
                    (project, limit),
                )

            samples = []
            for row in cursor.fetchall():
                samples.append(
                    MetricsSample(
                        project=row["project_name"],
                        collected_at=row["collected_at"],
                        metric_type=row["metric_type"],
                        cpu_percent=row["cpu_percent"],
                        memory_rss_bytes=row["memory_rss_bytes"],
                        memory_percent=row["memory_percent"],
                        disk_used_bytes=row["disk_used_bytes"],
                        process_count=row["process_count"],
                        requests_total=row["requests_total"],
                        requests_2xx=row["requests_2xx"],
                        requests_4xx=row["requests_4xx"],
                        requests_5xx=row["requests_5xx"],
                        avg_response_ms=row["avg_response_ms"],
                        p95_response_ms=row["p95_response_ms"],
                        db_size_bytes=row["db_size_bytes"],
                        db_connections=row["db_connections"],
                    )
                )

            return samples

    def get_summary(
        self,
        project: str,
        since: str = "1h",
    ) -> MetricsSummary:
        """Get aggregated metrics summary for a project.

        Args:
            project: Project name
            since: Duration (e.g., "1h", "24h", "7d")
        """
        self._validate_project(project)
        start_time = self._parse_since(since)
        end_time = datetime.utcnow()

        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as sample_count,
                    AVG(cpu_percent) as cpu_avg,
                    MAX(cpu_percent) as cpu_max,
                    AVG(memory_percent) as memory_avg,
                    MAX(memory_percent) as memory_max,
                    SUM(requests_total) as total_requests,
                    SUM(requests_2xx) as total_2xx,
                    SUM(requests_4xx) as total_4xx,
                    SUM(requests_5xx) as total_5xx,
                    AVG(avg_response_ms) as avg_response_ms,
                    MAX(p95_response_ms) as p95_response_ms,
                    AVG(db_connections) as db_connections_avg
                FROM metrics
                WHERE project_name = ? AND collected_at >= ?
                """,
                (project, start_time.isoformat()),
            )
            row = cursor.fetchone()

            # Get latest database size
            cursor2 = conn.execute(
                """
                SELECT db_size_bytes FROM metrics
                WHERE project_name = ? AND db_size_bytes IS NOT NULL
                ORDER BY collected_at DESC
                LIMIT 1
                """,
                (project,),
            )
            db_row = cursor2.fetchone()

        # Calculate error rate
        error_rate = None
        if row["total_requests"] and row["total_requests"] > 0:
            total_errors = (row["total_4xx"] or 0) + (row["total_5xx"] or 0)
            error_rate = round((total_errors / row["total_requests"]) * 100, 2)

        return MetricsSummary(
            project=project,
            period_start=start_time.isoformat(),
            period_end=end_time.isoformat(),
            sample_count=row["sample_count"] or 0,
            cpu_avg=round(row["cpu_avg"], 2) if row["cpu_avg"] else None,
            cpu_max=round(row["cpu_max"], 2) if row["cpu_max"] else None,
            memory_avg=round(row["memory_avg"], 2) if row["memory_avg"] else None,
            memory_max=round(row["memory_max"], 2) if row["memory_max"] else None,
            total_requests=row["total_requests"],
            total_2xx=row["total_2xx"],
            total_4xx=row["total_4xx"],
            total_5xx=row["total_5xx"],
            error_rate=error_rate,
            avg_response_ms=round(row["avg_response_ms"], 2) if row["avg_response_ms"] else None,
            p95_response_ms=round(row["p95_response_ms"], 2) if row["p95_response_ms"] else None,
            db_size_latest=db_row["db_size_bytes"] if db_row else None,
            db_connections_avg=round(row["db_connections_avg"], 2)
            if row["db_connections_avg"]
            else None,
        )

    def _parse_since(self, since: str) -> datetime:
        """Parse a since parameter into a datetime.

        Supports:
            - ISO timestamps (e.g., "2025-12-15T00:00:00")
            - Durations (e.g., "1h", "24h", "7d", "30m")
        """
        # Try ISO format first
        try:
            return datetime.fromisoformat(since.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            pass

        # Parse duration
        match = re.match(r"^(\d+)([hdm])$", since.lower())
        if not match:
            raise MetricsServiceError(
                code="INVALID_SINCE",
                message=f"Invalid since format: {since}",
                suggestion="Use formats like '1h', '24h', '7d', '30m'",
            )

        value = int(match.group(1))
        unit = match.group(2)

        now = datetime.utcnow()
        if unit == "h":
            return now - timedelta(hours=value)
        elif unit == "d":
            return now - timedelta(days=value)
        elif unit == "m":
            return now - timedelta(minutes=value)

        return now

    # -------------------------------------------------------------------------
    # Threshold Checking and Alerts
    # -------------------------------------------------------------------------

    def check_thresholds(
        self, sample: MetricsSample, config: MetricsConfig
    ) -> list[ThresholdAlert]:
        """Check a metrics sample against thresholds.

        Returns list of alerts for any exceeded thresholds.
        """
        alerts = []

        # Get thresholds (use config values if set, otherwise defaults)
        thresholds = {
            "cpu_warning": config.cpu_warning_percent or DEFAULT_THRESHOLDS["cpu_warning_percent"],
            "cpu_critical": config.cpu_critical_percent
            or DEFAULT_THRESHOLDS["cpu_critical_percent"],
            "memory_warning": config.memory_warning_percent
            or DEFAULT_THRESHOLDS["memory_warning_percent"],
            "memory_critical": config.memory_critical_percent
            or DEFAULT_THRESHOLDS["memory_critical_percent"],
            "error_rate_warning": config.error_rate_warning_percent
            or DEFAULT_THRESHOLDS["error_rate_warning_percent"],
            "error_rate_critical": config.error_rate_critical_percent
            or DEFAULT_THRESHOLDS["error_rate_critical_percent"],
        }

        # Check CPU
        if sample.cpu_percent is not None:
            if sample.cpu_percent >= thresholds["cpu_critical"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="cpu_percent",
                        level="critical",
                        value=sample.cpu_percent,
                        threshold=thresholds["cpu_critical"],
                        message=f"CPU usage critical: {sample.cpu_percent:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )
            elif sample.cpu_percent >= thresholds["cpu_warning"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="cpu_percent",
                        level="warning",
                        value=sample.cpu_percent,
                        threshold=thresholds["cpu_warning"],
                        message=f"CPU usage high: {sample.cpu_percent:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )

        # Check memory
        if sample.memory_percent is not None:
            if sample.memory_percent >= thresholds["memory_critical"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="memory_percent",
                        level="critical",
                        value=sample.memory_percent,
                        threshold=thresholds["memory_critical"],
                        message=f"Memory usage critical: {sample.memory_percent:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )
            elif sample.memory_percent >= thresholds["memory_warning"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="memory_percent",
                        level="warning",
                        value=sample.memory_percent,
                        threshold=thresholds["memory_warning"],
                        message=f"Memory usage high: {sample.memory_percent:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )

        # Check error rate
        if sample.requests_total and sample.requests_total > 0:
            total_errors = (sample.requests_4xx or 0) + (sample.requests_5xx or 0)
            error_rate = (total_errors / sample.requests_total) * 100

            if error_rate >= thresholds["error_rate_critical"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="error_rate",
                        level="critical",
                        value=error_rate,
                        threshold=thresholds["error_rate_critical"],
                        message=f"Error rate critical: {error_rate:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )
            elif error_rate >= thresholds["error_rate_warning"]:
                alerts.append(
                    ThresholdAlert(
                        project=sample.project,
                        metric="error_rate",
                        level="warning",
                        value=error_rate,
                        threshold=thresholds["error_rate_warning"],
                        message=f"Error rate high: {error_rate:.1f}%",
                        timestamp=sample.collected_at,
                    )
                )

        return alerts

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def cleanup_old_metrics(self, project: str | None = None) -> int:
        """Delete metrics older than retention period.

        Args:
            project: Optional project name. If None, cleanup all projects.

        Returns:
            Number of deleted records.
        """
        deleted = 0

        with self.db.connection() as conn:
            if project:
                # Get retention for specific project
                cursor = conn.execute(
                    "SELECT retention_days FROM metrics_config WHERE project_name = ?",
                    (project,),
                )
                row = cursor.fetchone()
                retention_days = row["retention_days"] if row else 7

                cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
                cursor = conn.execute(
                    "DELETE FROM metrics WHERE project_name = ? AND collected_at < ?",
                    (project, cutoff),
                )
                deleted = cursor.rowcount
            else:
                # Cleanup all projects based on their retention settings
                cursor = conn.execute(
                    """
                    SELECT project_name, retention_days FROM metrics_config
                    """
                )
                configs = list(cursor.fetchall())

                for config in configs:
                    cutoff = (
                        datetime.utcnow() - timedelta(days=config["retention_days"])
                    ).isoformat()
                    cursor = conn.execute(
                        "DELETE FROM metrics WHERE project_name = ? AND collected_at < ?",
                        (config["project_name"], cutoff),
                    )
                    deleted += cursor.rowcount

                # Default cleanup for projects without config (7 days)
                default_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
                cursor = conn.execute(
                    """
                    DELETE FROM metrics
                    WHERE collected_at < ?
                    AND project_name NOT IN (SELECT project_name FROM metrics_config)
                    """,
                    (default_cutoff,),
                )
                deleted += cursor.rowcount

            conn.commit()

        return deleted

    def get_metrics_count(self, project: str) -> int:
        """Get the number of metrics samples stored for a project."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM metrics WHERE project_name = ?",
                (project,),
            )
            return cursor.fetchone()[0]

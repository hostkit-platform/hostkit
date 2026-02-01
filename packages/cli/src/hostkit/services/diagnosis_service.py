"""Failure pattern detection and diagnosis service for HostKit."""

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hostkit.database import get_db
from hostkit.services.health_service import HealthService
from hostkit.services.log_service import LogService
from hostkit.services.rate_limit_service import RateLimitService


@dataclass
class FailurePattern:
    """A detected failure pattern."""

    pattern_type: str
    severity: str  # "critical", "high", "medium", "low"
    occurrences: int
    window: str  # Human-readable window (e.g., "30m", "1h")
    common_error: str | None = None
    suggestion: str | None = None
    evidence: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "type": self.pattern_type,
            "severity": self.severity,
            "occurrences": self.occurrences,
            "window": self.window,
            "common_error": self.common_error,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "details": self.details,
        }


@dataclass
class DiagnosisResult:
    """Result of diagnosing a project."""

    project: str
    diagnosed_at: str
    overall_health: str  # "healthy", "degraded", "critical"
    patterns: list[FailurePattern] = field(default_factory=list)
    recent_failures: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    service_status: dict[str, Any] = field(default_factory=dict)
    database_status: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "diagnosed_at": self.diagnosed_at,
            "overall_health": self.overall_health,
            "patterns": [p.to_dict() for p in self.patterns],
            "recent_failures": self.recent_failures,
            "recommendations": self.recommendations,
            "service_status": self.service_status,
            "database_status": self.database_status,
        }


class DiagnosisError(Exception):
    """Exception for diagnosis errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Error pattern definitions for log analysis
ERROR_PATTERNS = [
    {
        "name": "missing_module",
        "pattern": (
            r"(?:ImportError|ModuleNotFoundError):\s*"
            r"(?:No module named\s*)?['\"]?([a-zA-Z0-9_.-]+)['\"]?"
        ),
        "severity": "high",
        "suggestion_template": (
            "Install missing module: pip install {module}"
            " or run 'hostkit deploy {project} --install'"
        ),
    },
    {
        "name": "port_conflict",
        "pattern": (
            r"(?:OSError|socket\.error).*"
            r"(?:Address already in use|bind\(\) failed).*?:?(\d+)?"
        ),
        "severity": "high",
        "suggestion_template": (
            "Port conflict detected. Run 'lsof -i :{port}'"
            " to find conflicting process, or change the port"
        ),
    },
    {
        "name": "permission_denied",
        "pattern": r"(?:PermissionError|Permission denied).*?([/\w.-]+)?",
        "severity": "medium",
        "suggestion_template": (
            "Check file permissions: 'chown -R {project}:{project} /home/{project}'"
        ),
    },
    {
        "name": "syntax_error",
        "pattern": r"SyntaxError:\s*(.+)",
        "severity": "critical",
        "suggestion_template": "Syntax error in code. Review recent changes: {error}",
    },
    {
        "name": "database_connection",
        "pattern": (
            r"(?:psycopg2\.OperationalError|Connection refused"
            r"|could not connect to server"
            r"|FATAL:\s*password authentication failed)"
        ),
        "severity": "critical",
        "suggestion_template": (
            "Database connection failed. Check PostgreSQL:"
            " 'systemctl status postgresql'"
            " and verify DATABASE_URL in .env"
        ),
    },
    {
        "name": "memory_error",
        "pattern": r"(?:MemoryError|Out of memory|Killed|OOM)",
        "severity": "critical",
        "suggestion_template": (
            "Memory exhaustion detected. Consider increasing"
            " memory limits or optimizing memory usage"
        ),
    },
    {
        "name": "file_not_found",
        "pattern": r"(?:FileNotFoundError|No such file or directory).*?['\"]([/\w.-]+)['\"]?",
        "severity": "medium",
        "suggestion_template": "Missing file: {file}. Check deployment includes all required files",
    },
    {
        "name": "timeout_error",
        "pattern": r"(?:TimeoutError|timed out|deadline exceeded)",
        "severity": "medium",
        "suggestion_template": (
            "Timeout errors detected. Check network connectivity and increase timeouts if needed"
        ),
    },
    {
        "name": "redis_connection",
        "pattern": r"(?:redis\.exceptions\.ConnectionError|Connection to Redis refused)",
        "severity": "high",
        "suggestion_template": (
            "Redis connection failed. Check Redis: 'systemctl status redis-server'"
        ),
    },
    {
        "name": "disk_full",
        "pattern": r"(?:No space left on device|disk quota exceeded|ENOSPC)",
        "severity": "critical",
        "suggestion_template": (
            "Disk space exhausted. Check disk usage: 'df -h' and clean up old files"
        ),
    },
]


class DiagnosisService:
    """Service for diagnosing project failures and suggesting fixes."""

    def __init__(self) -> None:
        self.db = get_db()
        self.log_service = LogService()
        self.rate_limit_service = RateLimitService()
        self.health_service = HealthService()

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists."""
        proj = self.db.get_project(project)
        if not proj:
            raise DiagnosisError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return proj

    def diagnose(
        self,
        project: str,
        verbose: bool = False,
        check_db: bool = False,
    ) -> DiagnosisResult:
        """Run full diagnosis on a project.

        Args:
            project: Project name
            verbose: Include raw log excerpts in evidence
            check_db: Also test database connectivity

        Returns:
            DiagnosisResult with all detected patterns and recommendations
        """
        self._validate_project(project)

        patterns: list[FailurePattern] = []
        recommendations: list[str] = []

        # Check for deploy crash loops
        crash_loop = self._detect_deploy_crash_loop(project)
        if crash_loop:
            patterns.append(crash_loop)
            if crash_loop.suggestion:
                recommendations.append(crash_loop.suggestion)

        # Analyze logs for error patterns
        log_patterns = self._analyze_logs(project, verbose=verbose)
        patterns.extend(log_patterns)
        for lp in log_patterns:
            if lp.suggestion and lp.suggestion not in recommendations:
                recommendations.append(lp.suggestion)

        # Check service status
        service_status = self._check_service_status(project)

        # Check database if requested
        database_status: dict[str, Any] = {}
        if check_db:
            database_status = self._check_database(project)
            if not database_status.get("connected"):
                patterns.append(
                    FailurePattern(
                        pattern_type="database_unreachable",
                        severity="critical",
                        occurrences=1,
                        window="now",
                        common_error=database_status.get("error", "Database connection failed"),
                        suggestion=(
                            "Check PostgreSQL:"
                            " 'systemctl status postgresql'"
                            " and verify DATABASE_URL"
                        ),
                    )
                )
                recommendations.append("Check PostgreSQL: 'systemctl status postgresql'")

        # Get recent failure stats
        recent_failures = self._get_recent_failure_stats(project)

        # Determine overall health
        overall_health = self._determine_overall_health(patterns, service_status)

        # Add recommendations based on patterns
        if not recommendations and patterns:
            recommendations.append(
                "Review the detected patterns and address them in priority order"
            )

        if overall_health == "critical" and "auto-pause" not in " ".join(recommendations).lower():
            recommendations.append("Consider enabling auto-pause to prevent further thrashing")

        return DiagnosisResult(
            project=project,
            diagnosed_at=datetime.utcnow().isoformat(),
            overall_health=overall_health,
            patterns=patterns,
            recent_failures=recent_failures,
            recommendations=recommendations,
            service_status=service_status,
            database_status=database_status,
        )

    def _detect_deploy_crash_loop(
        self,
        project: str,
        window_minutes: int = 30,
        threshold: int = 3,
    ) -> FailurePattern | None:
        """Detect rapid deploy-fail-restart cycles.

        Args:
            project: Project name
            window_minutes: Time window to analyze
            threshold: Minimum failures to consider a crash loop

        Returns:
            FailurePattern if crash loop detected, None otherwise
        """
        since = (datetime.utcnow() - timedelta(minutes=window_minutes)).isoformat()
        deploys = self.db.list_deploys(project, since=since, limit=50)

        if not deploys:
            return None

        # Count failures
        failures = [d for d in deploys if not d.get("success", True)]

        if len(failures) < threshold:
            return None

        # Check for repeated deploys (crash loop pattern)
        total_deploys = len(deploys)

        # Calculate time between deploys
        avg_interval_minutes = None
        if total_deploys >= 2:
            first_deploy = datetime.fromisoformat(deploys[-1]["deployed_at"])
            last_deploy = datetime.fromisoformat(deploys[0]["deployed_at"])
            total_minutes = (last_deploy - first_deploy).total_seconds() / 60
            if total_minutes > 0:
                avg_interval_minutes = round(total_minutes / (total_deploys - 1), 1)

        # Get common error messages
        error_messages = [f.get("error_message") for f in failures if f.get("error_message")]
        common_error = None
        if error_messages:
            # Find most common error
            from collections import Counter

            error_counts = Counter(error_messages)
            common_error = error_counts.most_common(1)[0][0]

        # Build evidence
        evidence = []
        for d in deploys[:5]:  # Last 5 deploys
            status = "failed" if not d.get("success", True) else "success"
            timestamp = (
                d["deployed_at"].split("T")[1][:8] if "T" in d["deployed_at"] else d["deployed_at"]
            )
            evidence.append(f"Deploy at {timestamp}: {status}")

        # Determine severity based on failure rate
        failure_rate = len(failures) / total_deploys if total_deploys > 0 else 0
        severity = "critical" if failure_rate > 0.7 else "high" if failure_rate > 0.5 else "medium"

        suggestion = "Check logs for errors: 'hostkit service logs {project}'. "
        if (
            common_error
            and "ModuleNotFoundError" in common_error
            or "ImportError" in str(common_error)
        ):
            suggestion += "Try: 'hostkit deploy {project} --install' to install dependencies"
        else:
            suggestion += "Fix the underlying issue before deploying again"

        return FailurePattern(
            pattern_type="deploy_crash_loop",
            severity=severity,
            occurrences=len(failures),
            window=f"{window_minutes}m",
            common_error=common_error,
            suggestion=suggestion.format(project=project),
            evidence=evidence,
            details={
                "total_deploys": total_deploys,
                "failures": len(failures),
                "failure_rate": round(failure_rate * 100, 1),
                "avg_interval_minutes": avg_interval_minutes,
            },
        )

    def _analyze_logs(
        self,
        project: str,
        lines: int = 500,
        verbose: bool = False,
    ) -> list[FailurePattern]:
        """Extract error patterns from recent logs.

        Args:
            project: Project name
            lines: Number of log lines to analyze
            verbose: Include raw log excerpts

        Returns:
            List of detected patterns
        """
        patterns: list[FailurePattern] = []

        # Get journal logs
        try:
            log_entries = self.log_service.get_journal_logs(project, lines=lines)
        except Exception:
            log_entries = []

        # Also read error log file if it exists
        try:
            file_entries = self.log_service.read_log_file(project, "error.log", lines=lines)
            log_entries.extend(file_entries)
        except Exception:
            pass

        # Combine all log messages
        log_text = "\n".join([e.message for e in log_entries if e.message])

        if not log_text:
            return patterns

        # Track pattern occurrences
        detected: dict[str, dict[str, Any]] = {}

        for error_def in ERROR_PATTERNS:
            matches = list(re.finditer(error_def["pattern"], log_text, re.IGNORECASE))

            if not matches:
                continue

            pattern_name = error_def["name"]

            # Extract details from first match for suggestion
            match = matches[0]
            groups = match.groups()

            # Build suggestion
            suggestion = error_def["suggestion_template"].format(
                project=project,
                module=groups[0] if groups else "unknown",
                port=groups[0] if groups and groups[0] and groups[0].isdigit() else "8000",
                file=groups[0] if groups else "unknown",
                error=match.group(0)[:100] if match else "unknown",
            )

            # Collect evidence
            evidence = []
            if verbose:
                for m in matches[:3]:  # Up to 3 examples
                    # Get surrounding context
                    start = max(0, m.start() - 50)
                    end = min(len(log_text), m.end() + 50)
                    context = log_text[start:end].replace("\n", " ").strip()
                    evidence.append(context[:200])

            detected[pattern_name] = {
                "occurrences": len(matches),
                "common_error": match.group(0)[:200],
                "suggestion": suggestion,
                "evidence": evidence,
                "severity": error_def["severity"],
            }

        # Convert to FailurePattern objects
        for pattern_name, data in detected.items():
            patterns.append(
                FailurePattern(
                    pattern_type=pattern_name,
                    severity=data["severity"],
                    occurrences=data["occurrences"],
                    window="recent",
                    common_error=data["common_error"],
                    suggestion=data["suggestion"],
                    evidence=data["evidence"],
                )
            )

        # Sort by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        patterns.sort(key=lambda p: severity_order.get(p.severity, 4))

        return patterns

    def _check_service_status(self, project: str) -> dict[str, Any]:
        """Check systemd service status.

        Returns dict with:
            - running: bool
            - status: str (active, inactive, failed)
            - recent_restarts: int
            - last_failure: str | None
        """
        service_name = f"hostkit-{project}"
        result: dict[str, Any] = {
            "running": False,
            "status": "unknown",
            "recent_restarts": 0,
            "last_failure": None,
            "exit_code": None,
        }

        try:
            # Check service status
            status_result = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = status_result.stdout.strip()
            result["status"] = status
            result["running"] = status == "active"

            # If failed, get more details
            if status == "failed":
                show_result = subprocess.run(
                    [
                        "systemctl",
                        "show",
                        f"{service_name}.service",
                        "--property=ExecMainStatus,Result,ActiveEnterTimestamp",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in show_result.stdout.strip().split("\n"):
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key == "ExecMainStatus":
                            try:
                                result["exit_code"] = int(value)
                            except ValueError:
                                pass
                        elif key == "Result":
                            result["last_failure"] = value

            # Count recent restarts from journal
            restart_result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    f"{service_name}.service",
                    "--since",
                    "1 hour ago",
                    "--no-pager",
                    "-o",
                    "cat",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if restart_result.returncode == 0:
                # Count "Started" entries
                restarts = restart_result.stdout.count("Started") + restart_result.stdout.count(
                    "Stopped"
                )
                result["recent_restarts"] = max(0, restarts - 1)  # Don't count initial start

        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass

        return result

    def _check_database(self, project: str) -> dict[str, Any]:
        """Check database connectivity.

        Returns dict with:
            - connected: bool
            - error: str | None
            - latency_ms: float | None
        """
        import time

        result: dict[str, Any] = {
            "connected": False,
            "error": None,
            "latency_ms": None,
        }

        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            result["error"] = "No .env file found"
            return result

        # Parse DATABASE_URL
        database_url = None
        try:
            content = env_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError as e:
            result["error"] = f"Cannot read .env: {e}"
            return result

        if not database_url:
            result["error"] = "DATABASE_URL not configured"
            return result

        # Test connection
        try:
            start = time.time()
            check_result = subprocess.run(
                ["psql", database_url, "-c", "SELECT 1", "-t", "-A"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            latency = (time.time() - start) * 1000

            if check_result.returncode == 0:
                result["connected"] = True
                result["latency_ms"] = round(latency, 2)
            else:
                result["error"] = (
                    check_result.stderr.strip()[:200]
                    if check_result.stderr
                    else "Connection failed"
                )

        except subprocess.TimeoutExpired:
            result["error"] = "Connection timeout"
        except subprocess.SubprocessError as e:
            result["error"] = str(e)

        return result

    def _get_recent_failure_stats(self, project: str) -> dict[str, Any]:
        """Get statistics about recent failures.

        Returns dict with:
            - deploys_1h: int
            - failures_1h: int
            - service_crashes_1h: int
            - consecutive_failures: int
        """
        stats: dict[str, Any] = {
            "deploys_1h": 0,
            "failures_1h": 0,
            "service_crashes_1h": 0,
            "consecutive_failures": 0,
        }

        # Get deploy stats
        since_1h = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        deploys = self.db.list_deploys(project, since=since_1h, limit=100)

        stats["deploys_1h"] = len(deploys)
        stats["failures_1h"] = len([d for d in deploys if not d.get("success", True)])

        # Get consecutive failures
        stats["consecutive_failures"] = self.db.get_consecutive_failures(project)

        # Estimate service crashes from journal
        try:
            crash_result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    f"hostkit-{project}.service",
                    "--since",
                    "1 hour ago",
                    "--no-pager",
                    "-o",
                    "cat",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if crash_result.returncode == 0:
                # Count failure indicators
                output = crash_result.stdout
                crashes = (
                    output.count("Failed to start")
                    + output.count("failed with result")
                    + output.count("Main process exited")
                )
                stats["service_crashes_1h"] = crashes
        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass

        return stats

    def _determine_overall_health(
        self,
        patterns: list[FailurePattern],
        service_status: dict[str, Any],
    ) -> str:
        """Determine overall health based on patterns and status.

        Returns:
            "healthy", "degraded", or "critical"
        """
        # Critical if service not running
        if not service_status.get("running", False):
            return "critical"

        # Critical if any critical severity patterns
        if any(p.severity == "critical" for p in patterns):
            return "critical"

        # Degraded if high severity patterns
        if any(p.severity == "high" for p in patterns):
            return "degraded"

        # Degraded if multiple medium severity patterns
        medium_patterns = [p for p in patterns if p.severity == "medium"]
        if len(medium_patterns) >= 2:
            return "degraded"

        # Degraded if service has recent restarts
        if service_status.get("recent_restarts", 0) > 3:
            return "degraded"

        return "healthy"

    def get_quick_status(self, project: str) -> dict[str, Any]:
        """Get a quick status check without full diagnosis.

        Returns:
            dict with health, recent_failures, and service_running
        """
        self._validate_project(project)

        service_status = self._check_service_status(project)
        recent_failures = self._get_recent_failure_stats(project)

        # Quick health determination
        health = "healthy"
        if not service_status.get("running"):
            health = "critical"
        elif recent_failures.get("consecutive_failures", 0) >= 3:
            health = "degraded"
        elif recent_failures.get("failures_1h", 0) > 5:
            health = "degraded"

        return {
            "project": project,
            "health": health,
            "service_running": service_status.get("running", False),
            "service_status": service_status.get("status", "unknown"),
            "consecutive_failures": recent_failures.get("consecutive_failures", 0),
            "failures_1h": recent_failures.get("failures_1h", 0),
        }

    def run_startup_test(
        self,
        project: str,
        timeout_seconds: int = 10,
        restart_after: bool = True,
    ) -> dict[str, Any]:
        """Run the project's entrypoint directly and capture output.

        This is useful for debugging startup crashes where systemd only shows
        'exit code 1' without the actual error message.

        Args:
            project: Project name
            timeout_seconds: How long to let the process run (default: 10)
            restart_after: Whether to restart the service after the test

        Returns:
            dict with:
                - exit_code: int or None if timed out
                - stdout: captured stdout
                - stderr: captured stderr
                - timed_out: bool
                - runtime: project runtime type
                - command: the command that was run
                - service_was_running: whether service was running before test
                - service_restarted: whether service was restarted after
        """
        proj = self._validate_project(project)
        runtime = proj.get("runtime", "python")

        # Determine the start command based on runtime
        start_commands = {
            "python": f"/home/{project}/venv/bin/python -m app",
            "node": f"/usr/bin/node /home/{project}/app/index.js",
            "nextjs": "/usr/bin/npm start",
            "static": "/bin/true",
        }

        # Check for Next.js standalone mode
        standalone_server = Path(f"/home/{project}/app/server.js")
        if runtime == "nextjs" and standalone_server.exists():
            command = f"/usr/bin/node /home/{project}/app/server.js"
        else:
            command = start_commands.get(runtime, start_commands["python"])

        result: dict[str, Any] = {
            "project": project,
            "runtime": runtime,
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "service_was_running": False,
            "service_restarted": False,
        }

        # Check if service is running
        service_status = self._check_service_status(project)
        result["service_was_running"] = service_status.get("running", False)

        # Stop the service if it's running
        if result["service_was_running"]:
            try:
                subprocess.run(
                    ["systemctl", "stop", f"hostkit-{project}"],
                    capture_output=True,
                    timeout=10,
                )
                # Give it a moment to fully stop
                import time

                time.sleep(1)
            except subprocess.SubprocessError:
                pass

        # Run the entrypoint command as the project user
        try:
            # Build environment from project's .env file
            env = dict(os.environ)
            env_file = Path(f"/home/{project}/.env")
            if env_file.exists():
                try:
                    for line in env_file.read_text().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            # Strip quotes from value
                            value = value.strip().strip('"').strip("'")
                            env[key] = value
                except OSError:
                    pass

            proc = subprocess.run(
                ["sudo", "-u", project, "-E", "bash", "-c", f"cd /home/{project}/app && {command}"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                cwd=f"/home/{project}/app",
            )

            result["exit_code"] = proc.returncode
            result["stdout"] = proc.stdout[-10000:] if proc.stdout else ""  # Limit output
            result["stderr"] = proc.stderr[-10000:] if proc.stderr else ""
            result["timed_out"] = False

        except subprocess.TimeoutExpired as e:
            result["timed_out"] = True
            result["stdout"] = e.stdout.decode()[-10000:] if e.stdout else ""
            result["stderr"] = e.stderr.decode()[-10000:] if e.stderr else ""
            result["exit_code"] = None
            # Kill the process group
            try:
                subprocess.run(["pkill", "-f", command], capture_output=True, timeout=5)
            except subprocess.SubprocessError:
                pass

        except subprocess.SubprocessError as e:
            result["stderr"] = f"Failed to run command: {e}"
            result["exit_code"] = -1

        # Restart the service if requested and it was running before
        if restart_after and result["service_was_running"]:
            try:
                subprocess.run(
                    ["systemctl", "start", f"hostkit-{project}"],
                    capture_output=True,
                    timeout=10,
                )
                result["service_restarted"] = True
            except subprocess.SubprocessError:
                result["service_restarted"] = False

        return result

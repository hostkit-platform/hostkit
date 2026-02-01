"""Log management service for HostKit."""

import gzip
import re
import shutil
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class LogEntry:
    """A single log entry."""

    timestamp: str
    source: str  # "app", "error", "celery", "journal"
    level: str | None  # "INFO", "ERROR", "WARNING", etc.
    message: str
    file: str | None = None
    line_number: int | None = None


@dataclass
class LogSearchResult:
    """A search match with context."""

    file: str
    line_number: int
    match: str
    context_before: list[str]
    context_after: list[str]


@dataclass
class LogStats:
    """Log statistics for a project."""

    total_size: int
    file_count: int
    oldest_entry: str | None
    newest_entry: str | None
    error_count_24h: int
    warning_count_24h: int


class LogServiceError(Exception):
    """Base exception for log service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Log level priority for filtering
LOG_LEVELS = {
    "DEBUG": 0,
    "INFO": 1,
    "WARNING": 2,
    "WARN": 2,
    "ERROR": 3,
    "CRITICAL": 4,
    "FATAL": 4,
}

# Logrotate configuration template
LOGROTATE_CONFIG = """/var/log/projects/*/*.log {{
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 root root
    sharedscripts
    postrotate
        # Signal apps to reopen log files if needed
        systemctl reload hostkit-* 2>/dev/null || true
    endscript
}}
"""


class LogService:
    """Service for managing logs across HostKit projects."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.log_base = Path("/var/log/projects")
        self.logrotate_config = Path("/etc/logrotate.d/hostkit-projects")

    def _get_project_log_dir(self, project: str) -> Path:
        """Get the centralized log directory for a project."""
        return self.log_base / project

    def _get_project_home_log_dir(self, project: str) -> Path:
        """Get the home directory log symlink location."""
        return Path(f"/home/{project}/logs")

    def _validate_project(self, project: str) -> None:
        """Validate that a project exists."""
        proj = self.db.get_project(project)
        if not proj:
            raise LogServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def setup_log_directory(self, project: str) -> dict[str, Any]:
        """Set up log directory structure for a project."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)
        home_log_dir = self._get_project_home_log_dir(project)

        # Create centralized log directory
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create standard log files
        log_files = ["app.log", "error.log", "access.log"]
        for log_file in log_files:
            (log_dir / log_file).touch()

        # Set ownership
        subprocess.run(
            ["chown", "-R", f"{project}:{project}", str(log_dir)],
            check=True,
            capture_output=True,
        )

        # Create symlink from home directory
        if home_log_dir.is_symlink():
            home_log_dir.unlink()
        elif home_log_dir.exists():
            # If it's a real directory, remove it
            shutil.rmtree(home_log_dir)

        home_log_dir.symlink_to(log_dir)

        return {
            "project": project,
            "log_dir": str(log_dir),
            "symlink": str(home_log_dir),
            "files": log_files,
        }

    def setup_logrotate(self) -> dict[str, Any]:
        """Set up logrotate configuration for all HostKit projects."""
        self.logrotate_config.write_text(LOGROTATE_CONFIG)
        self.logrotate_config.chmod(0o644)

        return {
            "config_file": str(self.logrotate_config),
            "rotation": "daily",
            "keep": 7,
            "compress": True,
        }

    def get_log_files(self, project: str) -> list[dict[str, Any]]:
        """List all log files for a project."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)
        if not log_dir.exists():
            return []

        files = []
        for log_file in log_dir.iterdir():
            if log_file.is_file():
                stat = log_file.stat()
                files.append(
                    {
                        "name": log_file.name,
                        "path": str(log_file),
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "compressed": log_file.suffix == ".gz",
                    }
                )

        return sorted(files, key=lambda x: x["modified"], reverse=True)

    def get_journal_logs(
        self,
        project: str,
        lines: int = 100,
        since: str | None = None,
        until: str | None = None,
        priority: str | None = None,
    ) -> list[LogEntry]:
        """Get systemd journal logs for a project's services."""
        self._validate_project(project)

        entries = []

        # Get logs for main app service
        service_units = [f"hostkit-{project}"]

        # Check if worker service exists
        worker_service = Path(f"/etc/systemd/system/hostkit-{project}-worker.service")
        if worker_service.exists():
            service_units.append(f"hostkit-{project}-worker")

        for unit in service_units:
            cmd = ["journalctl", "-u", f"{unit}.service", "-n", str(lines), "-o", "json"]

            if since:
                cmd.extend(["--since", since])
            if until:
                cmd.extend(["--until", until])
            if priority:
                # Map log levels to journald priorities
                prio_map = {
                    "DEBUG": "7",
                    "INFO": "6",
                    "WARNING": "4",
                    "ERROR": "3",
                    "CRITICAL": "2",
                }
                if priority.upper() in prio_map:
                    cmd.extend(["-p", prio_map[priority.upper()]])

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    import json

                    for line in result.stdout.strip().split("\n"):
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            entries.append(
                                LogEntry(
                                    timestamp=datetime.fromtimestamp(
                                        int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1000000
                                    ).isoformat(),
                                    source="journal",
                                    level=self._priority_to_level(entry.get("PRIORITY", "6")),
                                    message=entry.get("MESSAGE", ""),
                                    file=unit,
                                )
                            )
                        except (json.JSONDecodeError, ValueError):
                            continue
            except subprocess.SubprocessError:
                continue

        return entries

    def _priority_to_level(self, priority: str) -> str:
        """Convert journald priority to log level."""
        prio_map = {
            "0": "CRITICAL",  # emerg
            "1": "CRITICAL",  # alert
            "2": "CRITICAL",  # crit
            "3": "ERROR",  # err
            "4": "WARNING",  # warning
            "5": "INFO",  # notice
            "6": "INFO",  # info
            "7": "DEBUG",  # debug
        }
        return prio_map.get(str(priority), "INFO")

    def read_log_file(
        self,
        project: str,
        filename: str = "app.log",
        lines: int = 100,
        level: str | None = None,
    ) -> list[LogEntry]:
        """Read entries from a log file."""
        self._validate_project(project)

        log_path = self._get_project_log_dir(project) / filename
        if not log_path.exists():
            return []

        entries = []
        level_threshold = LOG_LEVELS.get(level.upper(), 0) if level else 0

        # Handle compressed files
        if log_path.suffix == ".gz":
            opener = gzip.open
        else:
            opener = open

        try:
            with opener(log_path, "rt") as f:
                # Read all lines and take last N
                all_lines = f.readlines()
                recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

                for i, line in enumerate(recent_lines):
                    entry = self._parse_log_line(line.strip(), filename)
                    if entry:
                        # Filter by level
                        level_key = entry.level.upper() if entry.level else "INFO"
                        entry_level = LOG_LEVELS.get(level_key, 1)
                        if entry_level >= level_threshold:
                            entry.line_number = len(all_lines) - len(recent_lines) + i + 1
                            entries.append(entry)
        except Exception as e:
            raise LogServiceError(
                code="LOG_READ_FAILED",
                message=f"Failed to read log file: {e}",
            )

        return entries

    def _parse_log_line(self, line: str, source: str) -> LogEntry | None:
        """Parse a log line into a LogEntry."""
        if not line:
            return None

        # Try common log formats
        # Format 1: ISO timestamp with level: 2025-12-12T10:30:00 [INFO] message
        timestamp_re = (
            r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
            r"\s*\[?(\w+)\]?\s*(.*)$"
        )
        match = re.match(timestamp_re, line)
        if match:
            return LogEntry(
                timestamp=match.group(1),
                source=source.replace(".log", ""),
                level=match.group(2).upper() if match.group(2) in LOG_LEVELS else None,
                message=match.group(3),
                file=source,
            )

        # Format 2: Python logging style: INFO:module:message
        match = re.match(r"^(\w+):(\w+):(.*)$", line)
        if match and match.group(1).upper() in LOG_LEVELS:
            return LogEntry(
                timestamp=datetime.now().isoformat(),
                source=source.replace(".log", ""),
                level=match.group(1).upper(),
                message=match.group(3),
                file=source,
            )

        # Format 3: Simple timestamp: [2025-12-12 10:30:00] message
        match = re.match(r"^\[?(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]?\s*(.*)$", line)
        if match:
            return LogEntry(
                timestamp=match.group(1),
                source=source.replace(".log", ""),
                level=None,
                message=match.group(2),
                file=source,
            )

        # Fallback: treat whole line as message
        return LogEntry(
            timestamp=datetime.now().isoformat(),
            source=source.replace(".log", ""),
            level=None,
            message=line,
            file=source,
        )

    def tail_logs(
        self,
        project: str,
        sources: list[str] | None = None,
    ) -> Generator[LogEntry, None, None]:
        """Stream logs in real-time (generator for follow mode)."""
        self._validate_project(project)

        # Default to app and error logs plus journal
        if sources is None:
            sources = ["app.log", "error.log", "journal"]

        # Start tail processes for file-based logs
        processes = []
        log_dir = self._get_project_log_dir(project)

        for source in sources:
            if source == "journal":
                # Journal logs
                cmd = [
                    "journalctl",
                    "-u",
                    f"hostkit-{project}.service",
                    "-f",
                    "-o",
                    "cat",
                    "--no-pager",
                ]
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                processes.append(("journal", proc))
            else:
                # File-based logs
                log_path = log_dir / source
                if log_path.exists():
                    proc = subprocess.Popen(
                        ["tail", "-f", str(log_path)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    processes.append((source, proc))

        try:
            import select

            while processes:
                # Wait for any process to have output
                readable = [p[1].stdout for p in processes if p[1].stdout]
                if not readable:
                    break

                ready, _, _ = select.select(readable, [], [], 1.0)

                for stdout in ready:
                    # Find which process this belongs to
                    for source, proc in processes:
                        if proc.stdout == stdout:
                            line = stdout.readline()
                            if line:
                                entry = self._parse_log_line(line.strip(), source)
                                if entry:
                                    yield entry
                            break
        finally:
            # Clean up processes
            for _, proc in processes:
                proc.terminate()
                proc.wait()

    def search_logs(
        self,
        project: str,
        pattern: str,
        context: int = 2,
        files: list[str] | None = None,
        case_sensitive: bool = False,
    ) -> list[LogSearchResult]:
        """Search logs for a pattern with context."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)
        if not log_dir.exists():
            return []

        results = []
        regex_flags = 0 if case_sensitive else re.IGNORECASE

        try:
            compiled_pattern = re.compile(pattern, regex_flags)
        except re.error as e:
            raise LogServiceError(
                code="INVALID_PATTERN",
                message=f"Invalid regex pattern: {e}",
                suggestion="Check your regex syntax",
            )

        # Determine which files to search
        if files:
            search_files = [log_dir / f for f in files if (log_dir / f).exists()]
        else:
            search_files = [
                f for f in log_dir.iterdir() if f.is_file() and f.suffix in (".log", ".gz")
            ]

        for log_file in search_files:
            try:
                # Handle compressed files
                if log_file.suffix == ".gz":
                    opener = gzip.open
                else:
                    opener = open

                with opener(log_file, "rt") as f:
                    lines = f.readlines()

                for i, line in enumerate(lines):
                    if compiled_pattern.search(line):
                        start = max(0, i - context)
                        end = min(len(lines), i + context + 1)

                        results.append(
                            LogSearchResult(
                                file=str(log_file),
                                line_number=i + 1,
                                match=line.strip(),
                                context_before=[line.strip() for line in lines[start:i]],
                                context_after=[line.strip() for line in lines[i + 1 : end]],
                            )
                        )

            except Exception:
                # Skip files that can't be read
                continue

        return results

    def export_logs(
        self,
        project: str,
        output_path: str,
        since: str | None = None,
        until: str | None = None,
        compress: bool = True,
        include_journal: bool = True,
    ) -> dict[str, Any]:
        """Export logs to a file."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)
        output = Path(output_path)

        # Collect all log content
        all_logs: list[str] = []
        files_included = []

        # Add file-based logs
        if log_dir.exists():
            for log_file in sorted(log_dir.iterdir()):
                if log_file.is_file() and log_file.suffix == ".log":
                    try:
                        content = log_file.read_text()
                        all_logs.append(f"\n{'=' * 60}\n")
                        all_logs.append(f"=== {log_file.name} ===\n")
                        all_logs.append(f"{'=' * 60}\n\n")
                        all_logs.append(content)
                        files_included.append(log_file.name)
                    except Exception:
                        continue

        # Add journal logs
        if include_journal:
            cmd = ["journalctl", "-u", f"hostkit-{project}.service", "--no-pager"]
            if since:
                cmd.extend(["--since", since])
            if until:
                cmd.extend(["--until", until])

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if result.returncode == 0 and result.stdout:
                    all_logs.append(f"\n{'=' * 60}\n")
                    all_logs.append("=== systemd journal ===\n")
                    all_logs.append(f"{'=' * 60}\n\n")
                    all_logs.append(result.stdout)
                    files_included.append("systemd-journal")
            except subprocess.SubprocessError:
                pass

        # Write output
        combined = "".join(all_logs)

        if compress:
            if not output.suffix == ".gz":
                output = output.with_suffix(output.suffix + ".gz")
            with gzip.open(output, "wt") as f:
                f.write(combined)
        else:
            output.write_text(combined)

        return {
            "project": project,
            "output_file": str(output),
            "files_included": files_included,
            "size": output.stat().st_size,
            "compressed": compress,
        }

    def get_log_stats(self, project: str) -> LogStats:
        """Get log statistics for a project."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)

        total_size = 0
        file_count = 0
        oldest_entry = None
        newest_entry = None
        error_count = 0
        warning_count = 0

        if log_dir.exists():
            for log_file in log_dir.iterdir():
                if log_file.is_file():
                    stat = log_file.stat()
                    total_size += stat.st_size
                    file_count += 1

                    # Track oldest/newest by modification time
                    mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
                    if oldest_entry is None or mtime < oldest_entry:
                        oldest_entry = mtime
                    if newest_entry is None or mtime > newest_entry:
                        newest_entry = mtime

            # Count errors and warnings in last 24h
            for log_file in log_dir.iterdir():
                if log_file.is_file() and log_file.suffix == ".log":
                    try:
                        with open(log_file) as f:
                            for line in f:
                                line_upper = line.upper()
                                if "ERROR" in line_upper or "CRITICAL" in line_upper:
                                    error_count += 1
                                elif "WARNING" in line_upper or "WARN" in line_upper:
                                    warning_count += 1
                    except Exception:
                        continue

        return LogStats(
            total_size=total_size,
            file_count=file_count,
            oldest_entry=oldest_entry,
            newest_entry=newest_entry,
            error_count_24h=error_count,
            warning_count_24h=warning_count,
        )

    def get_aggregated_logs(
        self,
        project: str,
        lines: int = 100,
        level: str | None = None,
        sources: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[LogEntry]:
        """Get aggregated logs from multiple sources, sorted by timestamp."""
        self._validate_project(project)

        all_entries: list[LogEntry] = []

        # Default sources
        if sources is None:
            sources = ["app.log", "error.log", "journal"]

        # Parse time filters
        since_dt = self._parse_time_filter(since) if since else None
        until_dt = self._parse_time_filter(until) if until else None

        # Gather from file logs
        for source in sources:
            if source == "journal":
                entries = self.get_journal_logs(project, lines=lines, since=since, until=until)
                all_entries.extend(entries)
            else:
                entries = self.read_log_file(project, source, lines=lines * 2, level=level)
                all_entries.extend(entries)

        # Filter by time range
        if since_dt or until_dt:
            filtered = []
            for entry in all_entries:
                try:
                    entry_dt = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
                    # Make naive if comparison datetime is naive
                    if since_dt and since_dt.tzinfo is None and entry_dt.tzinfo is not None:
                        entry_dt = entry_dt.replace(tzinfo=None)
                    if until_dt and until_dt.tzinfo is None and entry_dt.tzinfo is not None:
                        entry_dt = entry_dt.replace(tzinfo=None)

                    if since_dt and entry_dt < since_dt:
                        continue
                    if until_dt and entry_dt > until_dt:
                        continue
                    filtered.append(entry)
                except (ValueError, TypeError):
                    # Include entries with unparseable timestamps
                    filtered.append(entry)
            all_entries = filtered

        # Sort by timestamp (newest first)
        all_entries.sort(key=lambda x: x.timestamp, reverse=True)

        # Return requested number of lines
        return all_entries[:lines]

    def _parse_time_filter(self, time_str: str) -> datetime:
        """Parse a time filter string into a datetime.

        Supports:
        - Relative: "1h", "30m", "24h", "7d"
        - ISO format: "2025-12-15", "2025-12-15T10:00:00"
        - Human readable: "1 hour ago", "2 days ago"
        """
        time_str = time_str.strip().lower()

        # Relative format: 1h, 30m, 24h, 7d
        relative_re = (
            r"^(\d+)\s*"
            r"(h|hour|hours|m|min|mins|minutes|d|day|days|w|week|weeks)$"
        )
        match = re.match(relative_re, time_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            now = datetime.now()
            if unit.startswith("h"):
                return now - timedelta(hours=value)
            elif unit.startswith("m"):
                return now - timedelta(minutes=value)
            elif unit.startswith("d"):
                return now - timedelta(days=value)
            elif unit.startswith("w"):
                return now - timedelta(weeks=value)

        # Human readable: "X hours/days ago"
        human_re = (
            r"^(\d+)\s*"
            r"(hour|hours|day|days|minute|minutes|week|weeks)"
            r"\s+ago$"
        )
        match = re.match(human_re, time_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            now = datetime.now()
            if "hour" in unit:
                return now - timedelta(hours=value)
            elif "day" in unit:
                return now - timedelta(days=value)
            elif "minute" in unit:
                return now - timedelta(minutes=value)
            elif "week" in unit:
                return now - timedelta(weeks=value)

        # ISO format
        try:
            # Try full ISO format
            return datetime.fromisoformat(time_str)
        except ValueError:
            pass

        # Date only
        try:
            return datetime.strptime(time_str, "%Y-%m-%d")
        except ValueError:
            pass

        raise LogServiceError(
            code="INVALID_TIME_FORMAT",
            message=f"Invalid time format: {time_str}",
            suggestion="Use formats like '1h', '24h', '7d', '2025-12-15', or '2 hours ago'",
        )

    def clear_logs(self, project: str, older_than_days: int = 0) -> dict[str, Any]:
        """Clear log files for a project."""
        self._validate_project(project)

        log_dir = self._get_project_log_dir(project)
        cleared_files = []
        cleared_size = 0

        if not log_dir.exists():
            return {"project": project, "cleared_files": [], "cleared_size": 0}

        now = datetime.now()

        for log_file in log_dir.iterdir():
            if log_file.is_file():
                stat = log_file.stat()
                file_age_days = (now - datetime.fromtimestamp(stat.st_mtime)).days

                if file_age_days >= older_than_days:
                    if log_file.suffix in (".log", ".gz"):
                        cleared_size += stat.st_size

                        if log_file.suffix == ".gz":
                            # Delete compressed files
                            log_file.unlink()
                            cleared_files.append(log_file.name)
                        else:
                            # Truncate active log files
                            log_file.write_text("")
                            cleared_files.append(f"{log_file.name} (truncated)")

        return {
            "project": project,
            "cleared_files": cleared_files,
            "cleared_size": cleared_size,
        }

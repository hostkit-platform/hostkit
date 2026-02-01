"""Cron job management service for HostKit using systemd timers."""

import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class ScheduledTask:
    """Information about a scheduled task."""

    id: str
    project: str
    name: str
    schedule: str
    schedule_cron: str | None
    command: str
    description: str | None
    enabled: bool
    created_at: str
    updated_at: str
    created_by: str | None
    last_run_at: str | None
    last_run_status: str | None
    last_run_exit_code: int | None
    timer_active: bool = False
    timer_enabled: bool = False

    @classmethod
    def from_db(cls, row: dict[str, Any]) -> "ScheduledTask":
        """Create from database row."""
        return cls(
            id=row["id"],
            project=row["project"],
            name=row["name"],
            schedule=row["schedule"],
            schedule_cron=row.get("schedule_cron"),
            command=row["command"],
            description=row.get("description"),
            enabled=bool(row.get("enabled", 1)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=row.get("created_by"),
            last_run_at=row.get("last_run_at"),
            last_run_status=row.get("last_run_status"),
            last_run_exit_code=row.get("last_run_exit_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "project": self.project,
            "name": self.name,
            "schedule": self.schedule,
            "schedule_cron": self.schedule_cron,
            "command": self.command,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "last_run_exit_code": self.last_run_exit_code,
            "timer_active": self.timer_active,
            "timer_enabled": self.timer_enabled,
        }


class CronError(Exception):
    """Exception for cron service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Common cron expression patterns to systemd OnCalendar
CRON_SHORTCUTS = {
    "@yearly": "*-01-01 00:00:00",
    "@annually": "*-01-01 00:00:00",
    "@monthly": "*-*-01 00:00:00",
    "@weekly": "Sun *-*-* 00:00:00",
    "@daily": "*-*-* 00:00:00",
    "@midnight": "*-*-* 00:00:00",
    "@hourly": "*-*-* *:00:00",
}


class CronService:
    """Service for managing scheduled tasks (cron jobs) using systemd timers."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.templates_dir = Path("/var/lib/hostkit/templates")
        self.systemd_dir = Path("/etc/systemd/system")

    def _validate_project(self, project: str) -> dict[str, Any]:
        """Validate that the project exists."""
        project_info = self.db.get_project(project)
        if not project_info:
            raise CronError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )
        return project_info

    def _validate_task_name(self, name: str) -> None:
        """Validate task name format."""
        if not re.match(r"^[a-z][a-z0-9-]*$", name):
            raise CronError(
                code="INVALID_TASK_NAME",
                message=f"Invalid task name '{name}'",
                suggestion="Task name must start with a letter and contain only lowercase letters, numbers, and hyphens",
            )
        if len(name) > 50:
            raise CronError(
                code="TASK_NAME_TOO_LONG",
                message="Task name must be 50 characters or less",
            )

    def _service_name(self, project: str, task_name: str) -> str:
        """Generate systemd service unit name."""
        return f"hostkit-{project}-cron-{task_name}"

    def _timer_name(self, project: str, task_name: str) -> str:
        """Generate systemd timer unit name."""
        return f"hostkit-{project}-cron-{task_name}"

    def cron_to_oncalendar(self, cron_expr: str) -> str:
        """Convert cron expression to systemd OnCalendar format.

        Supports:
        - Standard 5-field cron: minute hour day-of-month month day-of-week
        - Shortcuts: @yearly, @monthly, @weekly, @daily, @hourly
        - Already-valid OnCalendar expressions (passed through)

        Args:
            cron_expr: Cron expression or systemd OnCalendar format

        Returns:
            Systemd OnCalendar format string

        Examples:
            "0 3 * * *" -> "*-*-* 03:00:00" (daily at 3am)
            "30 4 * * 0" -> "Sun *-*-* 04:30:00" (Sundays at 4:30am)
            "0 */2 * * *" -> "*-*-* 00/2:00:00" (every 2 hours)
            "@daily" -> "*-*-* 00:00:00"
        """
        cron_expr = cron_expr.strip()

        # Check for shortcuts
        if cron_expr.lower() in CRON_SHORTCUTS:
            return CRON_SHORTCUTS[cron_expr.lower()]

        # Check if already in OnCalendar format (contains date-like pattern)
        if re.match(r"^\*?-", cron_expr) or re.match(r"^\d{4}-", cron_expr):
            return cron_expr

        # Also pass through common systemd patterns
        if any(kw in cron_expr.lower() for kw in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
            return cron_expr

        # Parse standard 5-field cron expression
        parts = cron_expr.split()
        if len(parts) != 5:
            raise CronError(
                code="INVALID_CRON_EXPRESSION",
                message=f"Invalid cron expression '{cron_expr}'",
                suggestion="Expected 5 fields: minute hour day-of-month month day-of-week, or use @daily, @hourly, etc.",
            )

        minute, hour, dom, month, dow = parts

        # Convert day-of-week names to systemd format
        dow_map = {
            "0": "Sun", "7": "Sun",
            "1": "Mon", "2": "Tue", "3": "Wed",
            "4": "Thu", "5": "Fri", "6": "Sat",
        }

        # Build day-of-week prefix if not wildcard
        dow_prefix = ""
        if dow != "*":
            # Handle ranges and lists
            if "," in dow or "-" in dow:
                # Complex DOW not fully supported, use as-is
                dow_parts = []
                for d in dow.replace("-", ",").split(","):
                    dow_parts.append(dow_map.get(d, d))
                dow_prefix = ",".join(dow_parts) + " "
            else:
                dow_prefix = dow_map.get(dow, dow) + " "

        # Convert fields
        def convert_field(val: str) -> str:
            """Convert cron field to systemd format."""
            if val == "*":
                return "*"
            # Handle */n step values
            if val.startswith("*/"):
                return f"00/{val[2:]}"
            # Handle ranges
            if "-" in val:
                return val
            # Handle lists
            if "," in val:
                return val
            # Zero-pad single values
            try:
                return f"{int(val):02d}"
            except ValueError:
                return val

        minute_str = convert_field(minute)
        hour_str = convert_field(hour)
        dom_str = convert_field(dom)
        month_str = convert_field(month)

        # Build OnCalendar string
        # Format: [day] YYYY-MM-DD HH:MM:SS
        date_part = f"{month_str}-{dom_str}"
        time_part = f"{hour_str}:{minute_str}:00"

        return f"{dow_prefix}*-{date_part} {time_part}"

    def _load_template(self, template_name: str) -> Template:
        """Load a Jinja2 template."""
        template_path = self.templates_dir / template_name
        if not template_path.exists():
            raise CronError(
                code="TEMPLATE_NOT_FOUND",
                message=f"Template '{template_name}' not found",
                suggestion="Ensure templates are synced to /var/lib/hostkit/templates/",
            )
        return Template(template_path.read_text())

    def _get_timer_status(self, timer_name: str) -> tuple[bool, bool]:
        """Get timer active and enabled status.

        Returns:
            Tuple of (is_active, is_enabled)
        """
        active = False
        enabled = False

        try:
            # Check active
            result = subprocess.run(
                ["systemctl", "is-active", f"{timer_name}.timer"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            active = result.returncode == 0

            # Check enabled
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{timer_name}.timer"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            enabled = result.stdout.strip() == "enabled"

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return active, enabled

    def _create_systemd_units(self, task: ScheduledTask) -> None:
        """Create systemd service and timer unit files."""
        service_name = self._service_name(task.project, task.name)

        # Load templates
        service_template = self._load_template("cron-task.service.j2")
        timer_template = self._load_template("cron-task.timer.j2")

        # Escape command for shell
        escaped_command = task.command.replace("'", "'\\''")

        # Render service file
        service_content = service_template.render(
            project_name=task.project,
            task_name=task.name,
            command=escaped_command,
        )

        # Render timer file
        timer_content = timer_template.render(
            project_name=task.project,
            task_name=task.name,
            schedule=task.schedule,
            description=task.description,
            randomized_delay=None,
        )

        # Write unit files
        service_path = self.systemd_dir / f"{service_name}.service"
        timer_path = self.systemd_dir / f"{service_name}.timer"

        service_path.write_text(service_content)
        timer_path.write_text(timer_content)

        # Reload systemd
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            timeout=10,
        )

    def _remove_systemd_units(self, project: str, task_name: str) -> None:
        """Remove systemd service and timer unit files."""
        service_name = self._service_name(project, task_name)

        # Stop and disable timer first
        try:
            subprocess.run(
                ["systemctl", "stop", f"{service_name}.timer"],
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["systemctl", "disable", f"{service_name}.timer"],
                capture_output=True,
                timeout=10,
            )
        except subprocess.SubprocessError:
            pass

        # Remove unit files
        service_path = self.systemd_dir / f"{service_name}.service"
        timer_path = self.systemd_dir / f"{service_name}.timer"

        if service_path.exists():
            service_path.unlink()
        if timer_path.exists():
            timer_path.unlink()

        # Reload systemd
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True,
            timeout=10,
        )

    def add_task(
        self,
        project: str,
        name: str,
        schedule: str,
        command: str,
        description: str | None = None,
    ) -> ScheduledTask:
        """Add a new scheduled task.

        Args:
            project: Project name
            name: Task name (alphanumeric and hyphens)
            schedule: Cron expression or systemd OnCalendar format
            command: Command to execute
            description: Optional description

        Returns:
            Created ScheduledTask
        """
        self._validate_project(project)
        self._validate_task_name(name)

        # Check if task already exists
        existing = self.db.get_scheduled_task(project, name)
        if existing:
            raise CronError(
                code="TASK_EXISTS",
                message=f"Task '{name}' already exists for project '{project}'",
                suggestion=f"Use 'hostkit cron remove {project} {name}' to delete it first",
            )

        # Convert cron expression to OnCalendar format
        oncalendar = self.cron_to_oncalendar(schedule)

        # Get current user for created_by
        created_by = os.environ.get("SUDO_USER") or os.environ.get("USER", "root")

        # Create database record
        task_id = str(uuid.uuid4())
        task_data = self.db.create_scheduled_task(
            task_id=task_id,
            project=project,
            name=name,
            schedule=oncalendar,
            command=command,
            schedule_cron=schedule if schedule != oncalendar else None,
            description=description,
            created_by=created_by,
        )

        task = ScheduledTask.from_db(task_data)

        # Create systemd units
        self._create_systemd_units(task)

        # Enable and start timer if task is enabled
        if task.enabled:
            service_name = self._service_name(project, name)
            subprocess.run(
                ["systemctl", "enable", f"{service_name}.timer"],
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["systemctl", "start", f"{service_name}.timer"],
                capture_output=True,
                timeout=10,
            )

        # Update timer status
        timer_name = self._timer_name(project, name)
        task.timer_active, task.timer_enabled = self._get_timer_status(timer_name)

        return task

    def remove_task(self, project: str, name: str) -> dict[str, Any]:
        """Remove a scheduled task.

        Args:
            project: Project name
            name: Task name

        Returns:
            Result dict with removed task info
        """
        self._validate_project(project)

        task_data = self.db.get_scheduled_task(project, name)
        if not task_data:
            raise CronError(
                code="TASK_NOT_FOUND",
                message=f"Task '{name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit cron list {project}' to see available tasks",
            )

        # Remove systemd units
        self._remove_systemd_units(project, name)

        # Delete from database
        self.db.delete_scheduled_task(project, name)

        return {
            "project": project,
            "name": name,
            "removed": True,
        }

    def list_tasks(self, project: str) -> list[ScheduledTask]:
        """List all scheduled tasks for a project.

        Args:
            project: Project name

        Returns:
            List of ScheduledTask objects
        """
        self._validate_project(project)

        tasks = []
        for row in self.db.list_scheduled_tasks(project):
            task = ScheduledTask.from_db(row)
            # Get timer status
            timer_name = self._timer_name(project, task.name)
            task.timer_active, task.timer_enabled = self._get_timer_status(timer_name)
            tasks.append(task)

        return tasks

    def get_task(self, project: str, name: str) -> ScheduledTask:
        """Get a specific task.

        Args:
            project: Project name
            name: Task name

        Returns:
            ScheduledTask object
        """
        self._validate_project(project)

        task_data = self.db.get_scheduled_task(project, name)
        if not task_data:
            raise CronError(
                code="TASK_NOT_FOUND",
                message=f"Task '{name}' not found for project '{project}'",
                suggestion=f"Run 'hostkit cron list {project}' to see available tasks",
            )

        task = ScheduledTask.from_db(task_data)
        timer_name = self._timer_name(project, name)
        task.timer_active, task.timer_enabled = self._get_timer_status(timer_name)

        return task

    def enable_task(self, project: str, name: str) -> ScheduledTask:
        """Enable a scheduled task.

        Args:
            project: Project name
            name: Task name

        Returns:
            Updated ScheduledTask
        """
        task = self.get_task(project, name)

        service_name = self._service_name(project, name)

        # Enable and start timer
        subprocess.run(
            ["systemctl", "enable", f"{service_name}.timer"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["systemctl", "start", f"{service_name}.timer"],
            capture_output=True,
            timeout=10,
        )

        # Update database
        self.db.update_scheduled_task(project, name, enabled=True)

        # Return updated task
        return self.get_task(project, name)

    def disable_task(self, project: str, name: str) -> ScheduledTask:
        """Disable a scheduled task.

        Args:
            project: Project name
            name: Task name

        Returns:
            Updated ScheduledTask
        """
        task = self.get_task(project, name)

        service_name = self._service_name(project, name)

        # Stop and disable timer
        subprocess.run(
            ["systemctl", "stop", f"{service_name}.timer"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["systemctl", "disable", f"{service_name}.timer"],
            capture_output=True,
            timeout=10,
        )

        # Update database
        self.db.update_scheduled_task(project, name, enabled=False)

        # Return updated task
        return self.get_task(project, name)

    def run_task(self, project: str, name: str) -> dict[str, Any]:
        """Run a scheduled task immediately.

        Args:
            project: Project name
            name: Task name

        Returns:
            Result dict with exit code and status
        """
        task = self.get_task(project, name)

        service_name = self._service_name(project, name)

        # Run the service (oneshot)
        result = subprocess.run(
            ["systemctl", "start", f"{service_name}.service"],
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max
        )

        exit_code = result.returncode
        status = "success" if exit_code == 0 else "failed"

        # Update last run info
        self.db.update_task_last_run(project, name, status, exit_code)

        return {
            "project": project,
            "name": name,
            "exit_code": exit_code,
            "status": status,
            "ran_at": datetime.utcnow().isoformat() + "Z",
        }

    def get_task_logs(
        self,
        project: str,
        name: str,
        lines: int = 50,
        follow: bool = False,
    ) -> str | None:
        """Get logs for a scheduled task.

        Args:
            project: Project name
            name: Task name
            lines: Number of lines to return
            follow: If True, follow logs (blocking)

        Returns:
            Log content or None if no logs
        """
        self.get_task(project, name)  # Validate task exists

        log_path = Path(f"/var/log/projects/{project}/cron-{name}.log")

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

    def get_next_run(self, project: str, name: str) -> str | None:
        """Get the next scheduled run time for a task.

        Args:
            project: Project name
            name: Task name

        Returns:
            Next run timestamp or None
        """
        task = self.get_task(project, name)

        if not task.timer_active:
            return None

        timer_name = self._timer_name(project, name)

        try:
            result = subprocess.run(
                ["systemctl", "show", f"{timer_name}.timer", "--property=NextElapseUSecRealtime"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("NextElapseUSecRealtime="):
                        value = line.split("=", 1)[1]
                        if value and value != "n/a":
                            return value
        except subprocess.SubprocessError:
            pass

        return None

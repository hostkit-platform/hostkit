"""Cron job management commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.cron_service import CronError, CronService


@click.group()
@click.pass_context
def cron(ctx: click.Context) -> None:
    """Manage scheduled tasks (cron jobs) using systemd timers.

    Schedule recurring tasks for your projects. Uses systemd timers
    for reliable execution and logging.

    Supports both cron expressions and systemd OnCalendar format:
        - "0 3 * * *" (cron: daily at 3am)
        - "@daily", "@hourly", "@weekly" (cron shortcuts)
        - "*-*-* 03:00:00" (systemd OnCalendar)
    """
    pass


@cron.command("add")
@click.argument("project")
@click.argument("name")
@click.argument("schedule")
@click.argument("command")
@click.option("--description", "-d", help="Description of what this task does")
@click.pass_context
@project_owner()
def cron_add(
    ctx: click.Context,
    project: str,
    name: str,
    schedule: str,
    command: str,
    description: str | None,
) -> None:
    """Add a new scheduled task.

    NAME must be lowercase letters, numbers, and hyphens only.
    SCHEDULE can be a cron expression or systemd OnCalendar format.
    COMMAND is executed in the project's app directory with .env loaded.

    Examples:

        # Daily cleanup at 3am
        hostkit cron add myapp cleanup "0 3 * * *" "python manage.py cleanup"

        # Every hour
        hostkit cron add myapp sync @hourly "python sync_data.py"

        # Weekly on Sunday at midnight
        hostkit cron add myapp report "0 0 * * 0" "python generate_report.py"

        # Using systemd OnCalendar format
        hostkit cron add myapp backup "*-*-* 02:30:00" "pg_dump > backup.sql"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        task = service.add_task(
            project=project,
            name=name,
            schedule=schedule,
            command=command,
            description=description,
        )

        formatter.success(
            message=f"Scheduled task '{name}' created for '{project}'",
            data=task.to_dict(),
        )

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("list")
@click.argument("project")
@click.pass_context
@project_owner()
def cron_list(ctx: click.Context, project: str) -> None:
    """List all scheduled tasks for a project.

    Shows task name, schedule, status, and last run information.

    Example:

        hostkit cron list myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        tasks = service.list_tasks(project)

        if not tasks:
            formatter.success(
                message=f"No scheduled tasks for '{project}'",
                data={"project": project, "tasks": []},
            )
            return

        data = [task.to_dict() for task in tasks]

        if formatter.json_mode:
            formatter.success(
                message=f"Found {len(tasks)} scheduled task(s)",
                data={"project": project, "tasks": data},
            )
        else:
            columns = [
                ("name", "Name"),
                ("schedule", "Schedule"),
                ("enabled", "Enabled"),
                ("timer_active", "Active"),
                ("last_run_status", "Last Status"),
                ("last_run_at", "Last Run"),
            ]
            # Format for display
            display_data = []
            for task in tasks:
                display_data.append(
                    {
                        "name": task.name,
                        "schedule": task.schedule_cron or task.schedule,
                        "enabled": "yes" if task.enabled else "no",
                        "timer_active": "yes" if task.timer_active else "no",
                        "last_run_status": task.last_run_status or "-",
                        "last_run_at": task.last_run_at[:19] if task.last_run_at else "-",
                    }
                )
            formatter.table(display_data, columns, title=f"Scheduled Tasks: {project}")

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("remove")
@click.argument("project")
@click.argument("name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner()
def cron_remove(
    ctx: click.Context,
    project: str,
    name: str,
    force: bool,
) -> None:
    """Remove a scheduled task.

    Stops the timer and removes the task configuration.

    Example:

        hostkit cron remove myapp cleanup
        hostkit cron remove myapp cleanup --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        # Get task info first
        task = service.get_task(project, name)

        if not force and not formatter.json_mode:
            if not click.confirm(
                f"Remove task '{name}' (schedule: {task.schedule_cron or task.schedule})?"
            ):
                click.echo("Cancelled")
                return

        result = service.remove_task(project, name)

        formatter.success(
            message=f"Scheduled task '{name}' removed from '{project}'",
            data=result,
        )

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("run")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner()
def cron_run(ctx: click.Context, project: str, name: str) -> None:
    """Run a scheduled task immediately.

    Executes the task now without waiting for the next scheduled time.
    Useful for testing or manual triggers.

    Example:

        hostkit cron run myapp cleanup
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        if not formatter.json_mode:
            click.echo(f"Running task '{name}'...")

        result = service.run_task(project, name)

        if result["status"] == "success":
            formatter.success(
                message=f"Task '{name}' completed successfully",
                data=result,
            )
        else:
            formatter.error(
                code="TASK_FAILED",
                message=f"Task '{name}' failed with exit code {result['exit_code']}",
                suggestion=f"Check logs with: hostkit cron logs {project} {name}",
            )
            raise SystemExit(result["exit_code"])

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("logs")
@click.argument("project")
@click.argument("name")
@click.option("--lines", "-n", type=int, default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_owner()
def cron_logs(
    ctx: click.Context,
    project: str,
    name: str,
    lines: int,
    follow: bool,
) -> None:
    """View logs for a scheduled task.

    Shows output from the task's log file.

    Examples:

        hostkit cron logs myapp cleanup
        hostkit cron logs myapp cleanup --lines 100
        hostkit cron logs myapp cleanup --follow
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        logs = service.get_task_logs(project, name, lines=lines, follow=follow)

        if follow:
            # Follow mode handles its own output
            return

        if logs is None:
            formatter.success(
                message=f"No logs found for task '{name}'",
                data={"project": project, "name": name, "logs": None},
            )
            return

        if formatter.json_mode:
            formatter.success(
                message=f"Logs for task '{name}'",
                data={"project": project, "name": name, "logs": logs},
            )
        else:
            click.echo(logs)

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("enable")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner()
def cron_enable(ctx: click.Context, project: str, name: str) -> None:
    """Enable a scheduled task.

    Starts the systemd timer to begin scheduling the task.

    Example:

        hostkit cron enable myapp cleanup
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        task = service.enable_task(project, name)

        formatter.success(
            message=f"Task '{name}' enabled",
            data=task.to_dict(),
        )

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("disable")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner()
def cron_disable(ctx: click.Context, project: str, name: str) -> None:
    """Disable a scheduled task.

    Stops the systemd timer. The task will not run until re-enabled.

    Example:

        hostkit cron disable myapp cleanup
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        task = service.disable_task(project, name)

        formatter.success(
            message=f"Task '{name}' disabled",
            data=task.to_dict(),
        )

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@cron.command("info")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner()
def cron_info(ctx: click.Context, project: str, name: str) -> None:
    """Show detailed information about a scheduled task.

    Example:

        hostkit cron info myapp cleanup
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = CronService()

    try:
        task = service.get_task(project, name)
        next_run = service.get_next_run(project, name)

        data = task.to_dict()
        data["next_run"] = next_run

        if formatter.json_mode:
            formatter.success(
                message=f"Task '{name}' info",
                data=data,
            )
        else:
            sections = {
                "task": {
                    "name": task.name,
                    "description": task.description or "(none)",
                    "command": task.command,
                },
                "schedule": {
                    "expression": task.schedule_cron or task.schedule,
                    "systemd_format": task.schedule,
                    "next_run": next_run or "(timer not active)",
                },
                "status": {
                    "enabled": "yes" if task.enabled else "no",
                    "timer_active": "yes" if task.timer_active else "no",
                    "timer_enabled": "yes" if task.timer_enabled else "no",
                },
                "last_run": {
                    "status": task.last_run_status or "(never run)",
                    "exit_code": task.last_run_exit_code
                    if task.last_run_exit_code is not None
                    else "-",
                    "time": task.last_run_at[:19] if task.last_run_at else "-",
                },
                "metadata": {
                    "created_by": task.created_by or "unknown",
                    "created_at": task.created_at[:19],
                    "updated_at": task.updated_at[:19],
                },
            }
            formatter.status_panel(
                f"Task: {task.name}", sections, message=f"Task info for '{name}'"
            )

    except CronError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

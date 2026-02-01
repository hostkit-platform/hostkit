"""Celery worker management commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.worker_service import WorkerService, WorkerError


@click.group()
@click.pass_context
def worker(ctx: click.Context) -> None:
    """Manage Celery background workers.

    Create, configure, and control Celery workers for your projects.
    Workers run as systemd services for reliable background task processing.

    Requires:
        - Redis running on the VPS (for broker)
        - Celery installed in project virtualenv
        - celery.py or app.py with Celery app configured
    """
    pass


@worker.command("add")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.option("--concurrency", "-c", type=int, default=2, help="Number of worker processes (default: 2)")
@click.option("--queues", "-q", help="Comma-separated list of queues")
@click.option("--app", "-A", "app_module", default="app", help="Celery app module (default: app)")
@click.option("--loglevel", "-l", default="info", help="Log level (default: info)")
@click.pass_context
@project_owner()
def worker_add(
    ctx: click.Context,
    project: str,
    worker_name: str,
    concurrency: int,
    queues: str | None,
    app_module: str,
    loglevel: str,
) -> None:
    """Add a new Celery worker.

    Creates a systemd service for the worker. The worker will start
    automatically and restart on failure.

    Examples:

        # Add default worker
        hostkit worker add myapp

        # Add worker with higher concurrency
        hostkit worker add myapp --concurrency 4

        # Add worker for specific queues
        hostkit worker add myapp --name email-worker --queues emails,notifications

        # Add with custom app module
        hostkit worker add myapp --app myapp.celery
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        worker_obj = service.add_worker(
            project=project,
            worker_name=worker_name,
            concurrency=concurrency,
            queues=queues,
            app_module=app_module,
            loglevel=loglevel,
        )

        formatter.success(
            message=f"Worker '{worker_name}' created for '{project}'",
            data=worker_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("remove")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner()
def worker_remove(
    ctx: click.Context,
    project: str,
    worker_name: str,
    force: bool,
) -> None:
    """Remove a Celery worker.

    Stops the worker and removes its systemd service.

    Example:

        hostkit worker remove myapp
        hostkit worker remove myapp --name email-worker --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        # Get worker info first
        worker_obj = service.get_worker(project, worker_name)

        if not force and not formatter.json_mode:
            if not click.confirm(
                f"Remove worker '{worker_name}' (concurrency: {worker_obj.concurrency})?"
            ):
                click.echo("Cancelled")
                return

        result = service.remove_worker(project, worker_name)

        formatter.success(
            message=f"Worker '{worker_name}' removed from '{project}'",
            data=result,
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("list")
@click.argument("project")
@click.pass_context
@project_owner()
def worker_list(ctx: click.Context, project: str) -> None:
    """List all workers for a project.

    Shows worker name, concurrency, queues, and status.

    Example:

        hostkit worker list myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        workers = service.list_workers(project)

        if not workers:
            formatter.success(
                message=f"No workers configured for '{project}'",
                data={"project": project, "workers": []},
            )
            return

        data = [w.to_dict() for w in workers]

        if formatter.json_mode:
            formatter.success(
                message=f"Found {len(workers)} worker(s)",
                data={"project": project, "workers": data},
            )
        else:
            columns = [
                ("worker_name", "Name"),
                ("concurrency", "Concurrency"),
                ("queues", "Queues"),
                ("service_active", "Active"),
                ("service_enabled", "Enabled"),
            ]
            # Format for display
            display_data = []
            for w in workers:
                display_data.append({
                    "worker_name": w.worker_name,
                    "concurrency": str(w.concurrency),
                    "queues": w.queues or "(all)",
                    "service_active": "yes" if w.service_active else "no",
                    "service_enabled": "yes" if w.service_enabled else "no",
                })
            formatter.table(display_data, columns, title=f"Workers: {project}")

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("status")
@click.argument("project")
@click.pass_context
@project_owner()
def worker_status(ctx: click.Context, project: str) -> None:
    """Show worker status for a project.

    Displays all workers and beat scheduler status.

    Example:

        hostkit worker status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        status = service.get_worker_status(project)

        if formatter.json_mode:
            formatter.success(
                message=f"Worker status for '{project}'",
                data=status,
            )
        else:
            workers = status["workers"]
            beat = status["beat"]

            sections = {
                "summary": {
                    "total_workers": status["worker_count"],
                    "active_workers": status["active_workers"],
                    "beat_enabled": "yes" if beat and beat["enabled"] else "no",
                    "beat_active": "yes" if beat and beat["service_active"] else "no",
                },
            }

            if workers:
                worker_info = {}
                for w in workers:
                    status_str = "running" if w["service_active"] else "stopped"
                    worker_info[w["worker_name"]] = f"{status_str} (concurrency: {w['concurrency']})"
                sections["workers"] = worker_info

            formatter.status_panel(f"Workers: {project}", sections, message=f"Worker status for '{project}'")

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("start")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.pass_context
@project_owner()
def worker_start(ctx: click.Context, project: str, worker_name: str) -> None:
    """Start a Celery worker.

    Example:

        hostkit worker start myapp
        hostkit worker start myapp --name email-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        worker_obj = service.start_worker(project, worker_name)

        formatter.success(
            message=f"Worker '{worker_name}' started",
            data=worker_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("stop")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.pass_context
@project_owner()
def worker_stop(ctx: click.Context, project: str, worker_name: str) -> None:
    """Stop a Celery worker.

    Example:

        hostkit worker stop myapp
        hostkit worker stop myapp --name email-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        worker_obj = service.stop_worker(project, worker_name)

        formatter.success(
            message=f"Worker '{worker_name}' stopped",
            data=worker_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("restart")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.pass_context
@project_owner()
def worker_restart(ctx: click.Context, project: str, worker_name: str) -> None:
    """Restart a Celery worker.

    Example:

        hostkit worker restart myapp
        hostkit worker restart myapp --name email-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        worker_obj = service.restart_worker(project, worker_name)

        formatter.success(
            message=f"Worker '{worker_name}' restarted",
            data=worker_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("scale")
@click.argument("project")
@click.argument("concurrency", type=int)
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.pass_context
@project_owner()
def worker_scale(
    ctx: click.Context,
    project: str,
    concurrency: int,
    worker_name: str,
) -> None:
    """Scale a worker's concurrency.

    Changes the number of worker processes. The worker will be
    restarted if currently running.

    Examples:

        hostkit worker scale myapp 4
        hostkit worker scale myapp 8 --name email-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        worker_obj = service.scale_worker(project, concurrency, worker_name)

        formatter.success(
            message=f"Worker '{worker_name}' scaled to {concurrency} processes",
            data=worker_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@worker.command("logs")
@click.argument("project")
@click.option("--name", "-n", "worker_name", default="default", help="Worker name (default: default)")
@click.option("--lines", "-l", type=int, default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_owner()
def worker_logs(
    ctx: click.Context,
    project: str,
    worker_name: str,
    lines: int,
    follow: bool,
) -> None:
    """View logs for a Celery worker.

    Examples:

        hostkit worker logs myapp
        hostkit worker logs myapp --name email-worker
        hostkit worker logs myapp --follow
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        logs = service.get_worker_logs(project, worker_name, lines=lines, follow=follow)

        if follow:
            # Follow mode handles its own output
            return

        if logs is None:
            formatter.success(
                message=f"No logs found for worker '{worker_name}'",
                data={"project": project, "worker_name": worker_name, "logs": None},
            )
            return

        if formatter.json_mode:
            formatter.success(
                message=f"Logs for worker '{worker_name}'",
                data={"project": project, "worker_name": worker_name, "logs": logs},
            )
        else:
            click.echo(logs)

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# Beat subgroup
@worker.group("beat")
@click.pass_context
def beat(ctx: click.Context) -> None:
    """Manage Celery beat scheduler.

    Beat is the periodic task scheduler. It sends tasks to workers
    according to a schedule defined in your Celery app.
    """
    pass


@beat.command("enable")
@click.argument("project")
@click.option("--app", "-A", "app_module", default="app", help="Celery app module (default: app)")
@click.option("--loglevel", "-l", default="info", help="Log level (default: info)")
@click.pass_context
@project_owner()
def beat_enable(
    ctx: click.Context,
    project: str,
    app_module: str,
    loglevel: str,
) -> None:
    """Enable Celery beat scheduler.

    Creates and starts a systemd service for beat.

    Example:

        hostkit worker beat enable myapp
        hostkit worker beat enable myapp --app myapp.celery
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        beat_obj = service.enable_beat(project, app_module, loglevel)

        formatter.success(
            message=f"Beat scheduler enabled for '{project}'",
            data=beat_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@beat.command("disable")
@click.argument("project")
@click.pass_context
@project_owner()
def beat_disable(ctx: click.Context, project: str) -> None:
    """Disable Celery beat scheduler.

    Stops and disables the beat service.

    Example:

        hostkit worker beat disable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        beat_obj = service.disable_beat(project)

        formatter.success(
            message=f"Beat scheduler disabled for '{project}'",
            data=beat_obj.to_dict(),
        )

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@beat.command("status")
@click.argument("project")
@click.pass_context
@project_owner()
def beat_status(ctx: click.Context, project: str) -> None:
    """Show beat scheduler status.

    Example:

        hostkit worker beat status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        beat_obj = service.get_beat_status(project)

        if beat_obj is None:
            formatter.success(
                message=f"Beat scheduler not configured for '{project}'",
                data={"project": project, "beat": None},
            )
            return

        if formatter.json_mode:
            formatter.success(
                message=f"Beat scheduler status for '{project}'",
                data=beat_obj.to_dict(),
            )
        else:
            sections = {
                "beat": {
                    "enabled": "yes" if beat_obj.enabled else "no",
                    "service_active": "yes" if beat_obj.service_active else "no",
                    "service_enabled": "yes" if beat_obj.service_enabled else "no",
                    "schedule_file": beat_obj.schedule_file,
                },
                "metadata": {
                    "created_at": beat_obj.created_at[:19],
                    "updated_at": beat_obj.updated_at[:19],
                },
            }
            formatter.status_panel(f"Beat: {project}", sections, message=f"Beat status for '{project}'")

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@beat.command("logs")
@click.argument("project")
@click.option("--lines", "-l", type=int, default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_owner()
def beat_logs(
    ctx: click.Context,
    project: str,
    lines: int,
    follow: bool,
) -> None:
    """View beat scheduler logs.

    Examples:

        hostkit worker beat logs myapp
        hostkit worker beat logs myapp --follow
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = WorkerService()

    try:
        logs = service.get_beat_logs(project, lines=lines, follow=follow)

        if follow:
            # Follow mode handles its own output
            return

        if logs is None:
            formatter.success(
                message=f"No logs found for beat scheduler",
                data={"project": project, "logs": None},
            )
            return

        if formatter.json_mode:
            formatter.success(
                message=f"Beat logs for '{project}'",
                data={"project": project, "logs": logs},
            )
        else:
            click.echo(logs)

    except WorkerError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

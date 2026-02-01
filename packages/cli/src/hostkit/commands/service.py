"""Service management commands for HostKit."""

import sys

import click

from hostkit.access import project_access, root_only, service_access
from hostkit.output import OutputFormatter
from hostkit.services.service_service import ServiceService, ServiceError


@click.group()
@click.pass_context
def service(ctx: click.Context) -> None:
    """Manage systemd services for projects.

    Each project has an app service, and optionally a worker service for Celery.
    """
    pass


@service.command("list")
@click.option("--project", "-p", help="Filter by project name")
@click.pass_context
def service_list(ctx: click.Context, project: str | None) -> None:
    """List all project services.

    Shows status, enabled state, and resource usage for each service.

    Example:
        hostkit service list
        hostkit service list --project myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        services = svc.list_services(project=project)

        if not services:
            formatter.success(
                message="No services found",
                data={"services": [], "count": 0},
            )
            return

        data = {
            "services": [
                {
                    "name": s.name,
                    "project": s.project,
                    "type": s.service_type,
                    "status": s.status,
                    "enabled": s.enabled,
                    "pid": s.pid,
                    "memory": s.memory,
                }
                for s in services
            ],
            "count": len(services),
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Found {len(services)} service(s)", data=data)
        else:
            click.echo("\nServices:")
            click.echo("-" * 80)
            click.echo(
                f"{'SERVICE':<30} {'PROJECT':<12} {'TYPE':<8} {'STATUS':<10} {'ENABLED':<8} {'PID':<8}"
            )
            click.echo("-" * 80)

            for s in services:
                status_colored = s.status
                if s.status == "running":
                    status_colored = click.style(s.status, fg="green")
                elif s.status == "failed":
                    status_colored = click.style(s.status, fg="red")
                else:
                    status_colored = click.style(s.status, fg="yellow")

                enabled_str = "yes" if s.enabled else "no"
                pid_str = str(s.pid) if s.pid else "-"

                click.echo(
                    f"{s.name:<30} {s.project:<12} {s.service_type:<8} {status_colored:<19} {enabled_str:<8} {pid_str:<8}"
                )

            click.echo("-" * 80)
            click.echo(f"Total: {len(services)} service(s)")

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("status")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_status(ctx: click.Context, name: str) -> None:
    """Show detailed status of a service.

    NAME can be the full service name (hostkit-myapp) or just the project name.

    Example:
        hostkit service status myapp
        hostkit service status hostkit-myapp-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        service_info = svc.get_service(name)

        data = {
            "name": service_info.name,
            "project": service_info.project,
            "type": service_info.service_type,
            "status": service_info.status,
            "enabled": service_info.enabled,
            "pid": service_info.pid,
            "memory": service_info.memory,
            "uptime": service_info.uptime,
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Status of {service_info.name}", data=data)
        else:
            click.echo(f"\nService: {service_info.name}")
            click.echo("=" * 50)
            click.echo(f"  Project:  {service_info.project}")
            click.echo(f"  Type:     {service_info.service_type}")

            status_str = service_info.status
            if service_info.status == "running":
                status_str = click.style(service_info.status, fg="green")
            elif service_info.status == "failed":
                status_str = click.style(service_info.status, fg="red")

            click.echo(f"  Status:   {status_str}")
            click.echo(f"  Enabled:  {'yes' if service_info.enabled else 'no'}")

            if service_info.pid:
                click.echo(f"  PID:      {service_info.pid}")
            if service_info.memory:
                click.echo(f"  Memory:   {service_info.memory}")
            if service_info.uptime:
                click.echo(f"  Since:    {service_info.uptime}")

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("start")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_start(ctx: click.Context, name: str) -> None:
    """Start a service.

    Example:
        hostkit service start myapp
        hostkit service start hostkit-myapp-worker
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.start(name)
        formatter.success(
            message=f"Service '{result['name']}' started",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("stop")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_stop(ctx: click.Context, name: str) -> None:
    """Stop a service.

    Example:
        hostkit service stop myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.stop(name)
        formatter.success(
            message=f"Service '{result['name']}' stopped",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("restart")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_restart(ctx: click.Context, name: str) -> None:
    """Restart a service.

    Example:
        hostkit service restart myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.restart(name)
        formatter.success(
            message=f"Service '{result['name']}' restarted",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("enable")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_enable(ctx: click.Context, name: str) -> None:
    """Enable a service to start on boot.

    Example:
        hostkit service enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.enable(name)
        formatter.success(
            message=f"Service '{result['name']}' enabled",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("disable")
@click.argument("name")
@click.pass_context
@service_access("name")
def service_disable(ctx: click.Context, name: str) -> None:
    """Disable a service from starting on boot.

    Example:
        hostkit service disable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.disable(name)
        formatter.success(
            message=f"Service '{result['name']}' disabled",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("logs")
@click.argument("name")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.option(
    "--systemd",
    is_flag=True,
    help="Show journalctl/systemd logs instead of app logs (useful for startup crashes)",
)
@click.option(
    "--error",
    is_flag=True,
    help="Show only error.log (stderr) instead of both app.log and error.log",
)
@click.pass_context
@service_access("name")
def service_logs(
    ctx: click.Context, name: str, lines: int, follow: bool, systemd: bool, error: bool
) -> None:
    """View service logs.

    By default, shows application logs from /var/log/projects/{project}/.
    Use --systemd to see journalctl output (useful for startup crashes).
    Use --error to see only stderr/error.log.
    Use --follow to stream logs in real-time.

    Examples:
        hostkit service logs myapp
        hostkit service logs myapp --lines 50
        hostkit service logs myapp --follow
        hostkit service logs myapp --systemd      # Show systemd/journalctl logs
        hostkit service logs myapp --error        # Show only stderr/error.log
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            log_type = "systemd" if systemd else ("error" if error else "app")
            click.echo(f"Following {log_type} logs for {name}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            proc = svc.get_logs(name, lines=lines, follow=True, systemd=systemd, error_only=error)
            try:
                for line in iter(proc.stdout.readline, b""):
                    sys.stdout.write(line.decode())
                    sys.stdout.flush()
            except KeyboardInterrupt:
                proc.terminate()
                click.echo("\n--- Log stream ended ---")
        else:
            logs = svc.get_logs(name, lines=lines, follow=False, systemd=systemd, error_only=error)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Logs for {name}",
                    data={
                        "logs": logs,
                        "lines": lines,
                        "source": "systemd" if systemd else "app",
                    },
                )
            else:
                log_type = "systemd/journalctl" if systemd else ("error" if error else "application")
                click.echo(f"\n{log_type.title()} logs for {name} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("create-worker")
@click.argument("project")
@click.option("--app-module", "-a", default="app", help="Celery app module (default: app)")
@click.option("--concurrency", "-c", default=2, help="Worker concurrency (default: 2)")
@click.pass_context
@root_only
def service_create_worker(
    ctx: click.Context, project: str, app_module: str, concurrency: int
) -> None:
    """Create a Celery worker service for a project.

    Creates a systemd service that runs a Celery worker for the project.
    The worker will use the project's virtual environment and .env file.

    Example:
        hostkit service create-worker myapp
        hostkit service create-worker myapp --app-module tasks --concurrency 4
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.create_worker(
            project=project, app_module=app_module, concurrency=concurrency
        )
        formatter.success(
            message=f"Celery worker created for '{project}'",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@service.command("delete-worker")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@root_only
def service_delete_worker(ctx: click.Context, project: str, force: bool) -> None:
    """Delete a Celery worker service.

    Stops and removes the worker service for the project.

    Example:
        hostkit service delete-worker myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    svc = ServiceService()

    try:
        result = svc.delete_worker(project=project, force=force)
        formatter.success(
            message=f"Celery worker deleted for '{project}'",
            data=result,
        )

    except ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

"""Health check command for HostKit projects."""

import sys
from datetime import datetime

import click

from hostkit.access import project_access
from hostkit.services.health_service import HealthCheck, HealthService, HealthServiceError
from hostkit.services.alert_service import AlertService


def _format_status(status: str) -> str:
    """Format status with color."""
    colors = {
        "healthy": "green",
        "degraded": "yellow",
        "unhealthy": "red",
    }
    return click.style(status.upper(), fg=colors.get(status, "white"), bold=True)


def _format_bool(value: bool | None, true_text: str = "Yes", false_text: str = "No") -> str:
    """Format boolean with color."""
    if value is None:
        return click.style("N/A", fg="white", dim=True)
    if value:
        return click.style(true_text, fg="green")
    return click.style(false_text, fg="red")


def _print_health_check(health: HealthCheck, verbose: bool = False) -> None:
    """Print health check results in human-readable format."""
    click.echo(f"\nHealth Check for '{health.project}'")
    click.echo("=" * 50)

    # Overall status
    click.echo(f"Overall Status: {_format_status(health.overall)}")
    click.echo()

    # Process status
    click.echo("Process:")
    click.echo(f"  Running: {_format_bool(health.process_running)}")
    if health.process_memory_mb is not None:
        click.echo(f"  Memory: {health.process_memory_mb:.1f} MB")
    if health.process_cpu_percent is not None:
        click.echo(f"  CPU: {health.process_cpu_percent:.1f}%")

    # HTTP status
    click.echo("\nHTTP Health:")
    if health.error:
        click.echo(f"  Status: {click.style('ERROR', fg='red')} - {health.error}")
    elif health.http_status:
        status_color = "green" if 200 <= health.http_status < 300 else "yellow" if health.http_status < 500 else "red"
        click.echo(f"  Status: {click.style(str(health.http_status), fg=status_color)}")
        if health.http_response_ms:
            click.echo(f"  Response Time: {health.http_response_ms:.0f}ms")
    else:
        click.echo(f"  Status: {click.style('Not checked', fg='white', dim=True)}")

    # Database
    if health.database_connected is not None:
        click.echo("\nDatabase:")
        click.echo(f"  Connected: {_format_bool(health.database_connected)}")

    # Auth service
    if health.auth_service_running is not None:
        click.echo("\nAuth Service:")
        click.echo(f"  Running: {_format_bool(health.auth_service_running)}")

    # Verbose output - show response body
    if verbose and health.http_body:
        click.echo("\nResponse Body (truncated):")
        click.echo(click.style(health.http_body[:200], dim=True))

    click.echo()


def _print_watch_header() -> None:
    """Print header for watch mode."""
    click.echo("\n" + "=" * 70)
    click.echo("Health Watch Mode - Press Ctrl+C to stop")
    click.echo("=" * 70)


def _print_watch_line(health: HealthCheck) -> None:
    """Print single-line health status for watch mode."""
    timestamp = datetime.now().strftime("%H:%M:%S")

    # Build status line
    status = _format_status(health.overall)

    parts = [
        f"[{timestamp}]",
        f"{health.project}:",
        status,
    ]

    # Add HTTP status if available
    if health.http_status:
        parts.append(f"HTTP:{health.http_status}")
    elif health.error:
        parts.append(f"HTTP:ERR")

    # Add response time
    if health.http_response_ms:
        parts.append(f"{health.http_response_ms:.0f}ms")

    # Add memory
    if health.process_memory_mb:
        parts.append(f"MEM:{health.process_memory_mb:.0f}MB")

    click.echo("  ".join(parts))


@click.command("health")
@click.argument("project")
@click.option(
    "--endpoint",
    "-e",
    default="/health",
    help="HTTP endpoint to check (default: /health)",
)
@click.option(
    "--watch",
    "-w",
    "watch_interval",
    type=int,
    default=None,
    help="Continuously monitor at N second intervals",
)
@click.option(
    "--expect",
    default=None,
    help="Expected content in response body",
)
@click.option(
    "--timeout",
    "-t",
    type=int,
    default=10,
    help="HTTP timeout in seconds (default: 10)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed output including response body",
)
@click.option(
    "--alert-on-failure",
    "-a",
    is_flag=True,
    help="Send alerts to configured channels on health check failure",
)
@click.pass_context
@project_access("project")
def health(
    ctx: click.Context,
    project: str,
    endpoint: str,
    watch_interval: int | None,
    expect: str | None,
    timeout: int,
    verbose: bool,
    alert_on_failure: bool,
) -> None:
    """Check the health of a project.

    Performs comprehensive health checks including:
    - Process status (systemd service)
    - HTTP health endpoint
    - Database connectivity (if configured)
    - Auth service status (if enabled)

    With --alert-on-failure, sends notifications to configured alert channels
    when the health check fails (unhealthy status).

    Examples:
        hostkit health myapp
        hostkit health myapp --endpoint /api/health
        hostkit health myapp --watch 30
        hostkit health myapp --expect "ok"
        hostkit health myapp --verbose
        hostkit health myapp --alert-on-failure
    """
    formatter = ctx.obj.get("formatter")
    json_mode = ctx.obj.get("json_mode", False)

    service = HealthService()
    alert_service = AlertService() if alert_on_failure else None

    # Track previous state for watch mode to avoid alert spam
    # Using a list to allow modification in nested functions
    state = {"last_status": None}

    def _send_health_alert(health_result: HealthCheck) -> None:
        """Send alert for health check failure."""
        if not alert_service:
            return

        data = {
            "overall": health_result.overall,
            "process_running": health_result.process_running,
            "http_status": health_result.http_status,
            "error": health_result.error,
            "endpoint": endpoint,
        }

        if health_result.http_response_ms:
            data["response_ms"] = health_result.http_response_ms

        try:
            alert_service.send_alert(
                project_name=project,
                event_type="health",
                event_status="failure",
                data=data,
            )
        except Exception:
            # Don't fail health check if alert fails
            pass

    def _send_recovery_alert(health_result: HealthCheck) -> None:
        """Send alert for health check recovery."""
        if not alert_service:
            return

        data = {
            "overall": health_result.overall,
            "process_running": health_result.process_running,
            "http_status": health_result.http_status,
            "endpoint": endpoint,
            "message": "Service has recovered",
        }

        try:
            alert_service.send_alert(
                project_name=project,
                event_type="health",
                event_status="success",
                data=data,
            )
        except Exception:
            pass

    try:
        if watch_interval:
            # Watch mode
            if json_mode:
                # In JSON mode, output each check as a JSON line
                try:
                    for health_result in service.watch_health(
                        project,
                        endpoint=endpoint,
                        interval=watch_interval,
                        timeout=timeout,
                        expected_content=expect,
                    ):
                        if formatter:
                            formatter.success(data=health_result.to_dict())
                        else:
                            import json
                            click.echo(json.dumps(health_result.to_dict()))

                        # Send alerts on state change
                        if alert_on_failure:
                            last = state["last_status"]
                            if health_result.overall == "unhealthy" and last != "unhealthy":
                                _send_health_alert(health_result)
                            elif health_result.overall != "unhealthy" and last == "unhealthy":
                                _send_recovery_alert(health_result)
                            state["last_status"] = health_result.overall
                except KeyboardInterrupt:
                    pass
            else:
                # Pretty watch mode
                _print_watch_header()
                try:
                    for health_result in service.watch_health(
                        project,
                        endpoint=endpoint,
                        interval=watch_interval,
                        timeout=timeout,
                        expected_content=expect,
                    ):
                        _print_watch_line(health_result)

                        # Send alerts on state change
                        if alert_on_failure:
                            last = state["last_status"]
                            if health_result.overall == "unhealthy" and last != "unhealthy":
                                _send_health_alert(health_result)
                                click.echo(click.style("  → Alert sent", fg="yellow"))
                            elif health_result.overall != "unhealthy" and last == "unhealthy":
                                _send_recovery_alert(health_result)
                                click.echo(click.style("  → Recovery alert sent", fg="green"))
                            state["last_status"] = health_result.overall
                except KeyboardInterrupt:
                    click.echo("\nStopped watching.")
        else:
            # Single check
            health_result = service.check_health(
                project,
                endpoint=endpoint,
                timeout=timeout,
                expected_content=expect,
            )

            if json_mode:
                if formatter:
                    formatter.success(data=health_result.to_dict())
                else:
                    import json
                    click.echo(json.dumps(health_result.to_dict()))
            else:
                _print_health_check(health_result, verbose=verbose)

            # Send alert on failure
            if alert_on_failure and health_result.overall == "unhealthy":
                _send_health_alert(health_result)
                if not json_mode:
                    click.echo(click.style("Alert sent to configured channels", fg="yellow"))

            # Exit with non-zero if unhealthy
            if health_result.overall == "unhealthy":
                sys.exit(1)

    except HealthServiceError as e:
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

"""Auto-pause configuration CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.auto_pause_service import AutoPauseService, AutoPauseError


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


def parse_duration(duration_str: str) -> int:
    """Parse a duration string like '1h', '30m', '2d' into minutes."""
    duration_str = duration_str.lower().strip()

    if duration_str.endswith("d"):
        return int(duration_str[:-1]) * 24 * 60
    elif duration_str.endswith("h"):
        return int(duration_str[:-1]) * 60
    elif duration_str.endswith("m"):
        return int(duration_str[:-1])
    else:
        # Assume minutes if no suffix
        return int(duration_str)


@click.group()
def autopause() -> None:
    """Auto-pause configuration.

    Automatically pause projects after repeated failures to prevent
    resource waste and AI agent thrashing.

    \b
    Default Settings:
      enabled              - False (opt-in)
      failure_threshold    - 5 failures
      window               - 10 minutes

    \b
    Usage:
      hostkit autopause show myapp
      hostkit autopause set myapp --enabled --threshold 5 --window 10m
      hostkit autopause set myapp --disabled
    """
    pass


@autopause.command("show")
@click.argument("project")
@click.pass_context
def show_config(ctx: click.Context, project: str) -> None:
    """Show auto-pause configuration and current status.

    Displays the configured settings and whether the project is currently paused.

    \b
    Examples:
      hostkit autopause show myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = AutoPauseService()
        config = service.get_config(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": config.project_name,
                    "config": {
                        "enabled": config.enabled,
                        "failure_threshold": config.failure_threshold,
                        "window_minutes": config.window_minutes,
                    },
                    "status": {
                        "paused": config.paused,
                        "paused_at": config.paused_at,
                        "paused_reason": config.paused_reason,
                    },
                },
                message="Auto-pause configuration retrieved",
            )
        else:
            click.echo(f"\nAuto-pause configuration for {project}:\n")

            click.echo(click.style("Configuration:", bold=True))
            enabled_color = "green" if config.enabled else "yellow"
            enabled_text = "Enabled" if config.enabled else "Disabled"
            click.echo(f"  Status:            {click.style(enabled_text, fg=enabled_color)}")
            click.echo(f"  Failure threshold: {config.failure_threshold} failures")
            click.echo(f"  Window:            {config.window_minutes} minutes")

            click.echo()
            click.echo(click.style("Current State:", bold=True))

            if config.paused:
                click.echo(f"  {click.style('PAUSED', fg='red', bold=True)}")
                if config.paused_at:
                    click.echo(f"  Paused at:  {config.paused_at}")
                if config.paused_reason:
                    click.echo(f"  Reason:     {config.paused_reason}")
                click.echo()
                click.echo(click.style("Run 'hostkit resume' to continue", fg="yellow"))
            else:
                click.echo(f"  {click.style('Running', fg='green')}")

            click.echo()

    except AutoPauseError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@autopause.command("set")
@click.argument("project")
@click.option("--enabled/--disabled", default=None, help="Enable or disable auto-pause")
@click.option("--threshold", type=int, help="Number of failures to trigger pause")
@click.option("--window", "window_str", help="Time window for counting failures (e.g., 10m, 1h)")
@click.pass_context
@project_owner("project")
def set_config(
    ctx: click.Context,
    project: str,
    enabled: bool | None,
    threshold: int | None,
    window_str: str | None,
) -> None:
    """Configure auto-pause settings for a project.

    \b
    Examples:
      hostkit autopause set myapp --enabled
      hostkit autopause set myapp --threshold 5 --window 10m
      hostkit autopause set myapp --disabled
      hostkit autopause set myapp --enabled --threshold 3 --window 5m  # Stricter
    """
    formatter = get_formatter(ctx)

    # Parse duration strings
    window_minutes = None

    try:
        if window_str:
            window_minutes = parse_duration(window_str)
    except ValueError as e:
        formatter.error(
            code="INVALID_DURATION",
            message=f"Invalid duration format: {e}",
            suggestion="Use format: 30m, 1h, or just a number for minutes",
        )
        raise SystemExit(1)

    # Check that at least one option was provided
    if enabled is None and threshold is None and window_minutes is None:
        formatter.error(
            code="NO_OPTIONS",
            message="No configuration options provided",
            suggestion="Use --enabled/--disabled, --threshold, or --window",
        )
        raise SystemExit(1)

    try:
        service = AutoPauseService()
        config = service.set_config(
            project_name=project,
            enabled=enabled,
            failure_threshold=threshold,
            window_minutes=window_minutes,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": config.project_name,
                    "enabled": config.enabled,
                    "failure_threshold": config.failure_threshold,
                    "window_minutes": config.window_minutes,
                },
                message="Auto-pause configuration updated",
            )
        else:
            status_color = "green" if config.enabled else "yellow"
            status_text = "Enabled" if config.enabled else "Disabled"

            click.echo(click.style("\nAuto-pause configuration updated:", fg="green", bold=True))
            click.echo(f"  Status:            {click.style(status_text, fg=status_color)}")
            click.echo(f"  Failure threshold: {config.failure_threshold}")
            click.echo(f"  Window:            {config.window_minutes} minutes")

            if config.enabled:
                click.echo()
                click.echo(
                    f"Project will auto-pause after {config.failure_threshold} failures "
                    f"in {config.window_minutes} minutes"
                )
            click.echo()

    except AutoPauseError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

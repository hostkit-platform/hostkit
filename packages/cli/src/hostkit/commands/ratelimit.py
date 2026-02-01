"""Rate limit configuration CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.rate_limit_service import RateLimitService, RateLimitError


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
def ratelimit() -> None:
    """Deployment rate limit configuration.

    Configure limits to prevent AI agents from entering deploy-crash-deploy loops.

    \b
    Default Limits:
      max_deploys              - 10 deploys per window
      window                   - 60 minutes
      cooldown                 - 5 minutes after consecutive failures
      failure_limit            - 3 consecutive failures trigger cooldown

    \b
    Usage:
      hostkit ratelimit show myapp
      hostkit ratelimit set myapp --max 10 --window 1h
      hostkit ratelimit reset myapp
    """
    pass


@ratelimit.command("show")
@click.argument("project")
@click.pass_context
def show_limits(ctx: click.Context, project: str) -> None:
    """Show rate limit configuration and current status.

    Displays both the configured limits and the current usage status
    (deploys in window, consecutive failures, cooldown state).

    \b
    Examples:
      hostkit ratelimit show myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = RateLimitService()
        status = service.get_status(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": status.project_name,
                    "rate_limiting_enabled": status.config.max_deploys > 0,
                    "config": {
                        "max_deploys": status.config.max_deploys,
                        "window_minutes": status.config.window_minutes,
                        "failure_cooldown_minutes": status.config.failure_cooldown_minutes,
                        "consecutive_failure_limit": status.config.consecutive_failure_limit,
                    },
                    "status": {
                        "deploys_in_window": status.deploys_in_window,
                        "consecutive_failures": status.consecutive_failures,
                        "in_cooldown": status.in_cooldown,
                        "cooldown_ends_at": status.cooldown_ends_at,
                        "is_blocked": status.is_blocked,
                        "block_reason": status.block_reason,
                    },
                },
                message="Rate limit status retrieved",
            )
        else:
            click.echo(f"\nRate limit configuration for {project}:\n")

            click.echo(click.style("Configuration:", bold=True))
            click.echo(f"  Max deploys:       {status.config.max_deploys} per window")
            click.echo(f"  Window:            {status.config.window_minutes} minutes")
            click.echo(f"  Failure cooldown:  {status.config.failure_cooldown_minutes} minutes")
            click.echo(f"  Failure limit:     {status.config.consecutive_failure_limit} consecutive")

            click.echo()
            click.echo(click.style("Current Status:", bold=True))

            # Deploys in window
            deploy_pct = (status.deploys_in_window / status.config.max_deploys * 100) if status.config.max_deploys else 0
            deploy_color = "green" if deploy_pct < 50 else ("yellow" if deploy_pct < 80 else "red")
            click.echo(f"  Deploys in window: {click.style(str(status.deploys_in_window), fg=deploy_color)}/{status.config.max_deploys}")

            # Consecutive failures
            failure_color = "green" if status.consecutive_failures == 0 else ("yellow" if status.consecutive_failures < status.config.consecutive_failure_limit else "red")
            click.echo(f"  Consecutive fails: {click.style(str(status.consecutive_failures), fg=failure_color)}/{status.config.consecutive_failure_limit}")

            # Cooldown status
            if status.in_cooldown:
                click.echo(f"  Cooldown:          {click.style('Active', fg='yellow')} (until {status.cooldown_ends_at})")
            else:
                click.echo(f"  Cooldown:          {click.style('None', fg='green')}")

            # Overall status
            click.echo()
            if status.config.max_deploys <= 0:
                click.echo(click.style("Status: Rate limiting DISABLED (unlimited deploys)", fg="cyan", bold=True))
            elif status.is_blocked:
                click.echo(click.style(f"BLOCKED: {status.block_reason}", fg="red", bold=True))
            else:
                click.echo(click.style("Status: Ready for deploys", fg="green"))

            click.echo()

    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@ratelimit.command("set")
@click.argument("project")
@click.option("--max", "max_deploys", type=int, help="Maximum deploys per window")
@click.option("--window", "window_str", help="Window duration (e.g., 1h, 30m)")
@click.option("--cooldown", "cooldown_str", help="Cooldown duration after failures (e.g., 5m, 10m)")
@click.option("--failure-limit", type=int, help="Consecutive failures before cooldown")
@click.pass_context
@project_owner("project")
def set_limits(
    ctx: click.Context,
    project: str,
    max_deploys: int | None,
    window_str: str | None,
    cooldown_str: str | None,
    failure_limit: int | None,
) -> None:
    """Configure rate limits for a project.

    \b
    Examples:
      hostkit ratelimit set myapp --max 10 --window 1h
      hostkit ratelimit set myapp --cooldown 10m --failure-limit 5
      hostkit ratelimit set myapp --max 5 --window 30m  # Stricter limits
    """
    formatter = get_formatter(ctx)

    # Parse duration strings
    window_minutes = None
    cooldown_minutes = None

    try:
        if window_str:
            window_minutes = parse_duration(window_str)
        if cooldown_str:
            cooldown_minutes = parse_duration(cooldown_str)
    except ValueError as e:
        formatter.error(
            code="INVALID_DURATION",
            message=f"Invalid duration format: {e}",
            suggestion="Use format: 30m, 1h, 2d, or just a number for minutes",
        )
        raise SystemExit(1)

    # Check that at least one option was provided
    if max_deploys is None and window_minutes is None and cooldown_minutes is None and failure_limit is None:
        formatter.error(
            code="NO_OPTIONS",
            message="No configuration options provided",
            suggestion="Use --max, --window, --cooldown, or --failure-limit",
        )
        raise SystemExit(1)

    try:
        service = RateLimitService()
        config = service.set_config(
            project_name=project,
            max_deploys=max_deploys,
            window_minutes=window_minutes,
            failure_cooldown_minutes=cooldown_minutes,
            consecutive_failure_limit=failure_limit,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": config.project_name,
                    "max_deploys": config.max_deploys,
                    "window_minutes": config.window_minutes,
                    "failure_cooldown_minutes": config.failure_cooldown_minutes,
                    "consecutive_failure_limit": config.consecutive_failure_limit,
                },
                message="Rate limit configuration updated",
            )
        else:
            click.echo(click.style("\nRate limits updated:", fg="green", bold=True))
            click.echo(f"  Max deploys:       {config.max_deploys}")
            click.echo(f"  Window:            {config.window_minutes} minutes")
            click.echo(f"  Failure cooldown:  {config.failure_cooldown_minutes} minutes")
            click.echo(f"  Failure limit:     {config.consecutive_failure_limit}")
            click.echo()

    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@ratelimit.command("disable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def disable_limits(ctx: click.Context, project: str) -> None:
    """Disable rate limiting for a project.

    Sets max_deploys to 0, which means unlimited deploys are allowed.
    Useful during active development when you're iterating rapidly.

    \b
    Examples:
      hostkit ratelimit disable myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = RateLimitService()
        config = service.set_config(project_name=project, max_deploys=0)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": config.project_name,
                    "max_deploys": config.max_deploys,
                    "rate_limiting": "disabled",
                },
                message="Rate limiting disabled",
            )
        else:
            click.echo(click.style("\nRate limiting disabled for " + project, fg="green", bold=True))
            click.echo("  Unlimited deploys are now allowed.")
            click.echo()
            click.echo("To re-enable, run:")
            click.echo(f"  hostkit ratelimit enable {project}")
            click.echo()

    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@ratelimit.command("enable")
@click.argument("project")
@click.option("--max", "max_deploys", type=int, default=10, help="Maximum deploys per window (default: 10)")
@click.option("--window", "window_str", default="1h", help="Window duration (default: 1h)")
@click.pass_context
@project_owner("project")
def enable_limits(ctx: click.Context, project: str, max_deploys: int, window_str: str) -> None:
    """Enable rate limiting for a project.

    Sets rate limits to the specified values (or defaults).
    Use this to re-enable rate limiting after disabling it.

    \b
    Examples:
      hostkit ratelimit enable myapp             # Default: 10 deploys per hour
      hostkit ratelimit enable myapp --max 30    # 30 deploys per hour
      hostkit ratelimit enable myapp --max 20 --window 30m  # 20 per 30 minutes
    """
    formatter = get_formatter(ctx)

    try:
        window_minutes = parse_duration(window_str)
    except ValueError as e:
        formatter.error(
            code="INVALID_DURATION",
            message=f"Invalid duration format: {e}",
            suggestion="Use format: 30m, 1h, 2d, or just a number for minutes",
        )
        raise SystemExit(1)

    try:
        service = RateLimitService()
        config = service.set_config(
            project_name=project,
            max_deploys=max_deploys,
            window_minutes=window_minutes,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": config.project_name,
                    "max_deploys": config.max_deploys,
                    "window_minutes": config.window_minutes,
                    "rate_limiting": "enabled",
                },
                message="Rate limiting enabled",
            )
        else:
            click.echo(click.style("\nRate limiting enabled for " + project, fg="green", bold=True))
            click.echo(f"  Max deploys:  {config.max_deploys}")
            click.echo(f"  Window:       {config.window_minutes} minutes")
            click.echo()

    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@ratelimit.command("reset")
@click.argument("project")
@click.option("--history", is_flag=True, help="Also clear deploy history")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def reset_limits(ctx: click.Context, project: str, history: bool, force: bool) -> None:
    """Reset rate limits to defaults.

    This removes any custom rate limit configuration for the project.
    Optionally also clears the deploy history.

    \b
    Examples:
      hostkit ratelimit reset myapp
      hostkit ratelimit reset myapp --history  # Also clear history
    """
    formatter = get_formatter(ctx)

    if not force and not formatter.json_mode:
        msg = "Reset rate limits to defaults"
        if history:
            msg += " and clear deploy history"
        click.echo(f"\nAbout to: {msg} for {project}")
        if not click.confirm("\nContinue?"):
            click.echo("Cancelled.")
            return

    try:
        service = RateLimitService()
        config_deleted = service.reset_config(project)
        history_deleted = 0

        if history:
            history_deleted = service.clear_history(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "config_reset": config_deleted,
                    "history_cleared": history_deleted if history else None,
                },
                message="Rate limits reset",
            )
        else:
            if config_deleted:
                click.echo(click.style("Rate limits reset to defaults", fg="green"))
            else:
                click.echo("No custom rate limits were configured (using defaults)")

            if history:
                click.echo(f"Cleared {history_deleted} deploy history entries")
            click.echo()

    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

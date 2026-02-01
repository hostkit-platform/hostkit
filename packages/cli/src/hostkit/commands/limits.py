"""Resource limits CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.limits_service import LimitsService, LimitsServiceError


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


def parse_size(size_str: str) -> int:
    """Parse a size string like '512M', '1G', '2048' into MB.

    Args:
        size_str: Size string with optional suffix (M, G)

    Returns:
        Size in MB
    """
    size_str = size_str.strip().upper()

    if size_str.endswith("G"):
        return int(float(size_str[:-1]) * 1024)
    elif size_str.endswith("M"):
        return int(size_str[:-1])
    else:
        # Assume MB if no suffix
        return int(size_str)


def format_size_mb(mb: int | None) -> str:
    """Format MB value to human-readable string."""
    if mb is None:
        return "Unlimited"
    if mb >= 1024:
        return f"{mb / 1024:.1f}G ({mb}MB)"
    return f"{mb}MB"


def format_cpu(quota: int | None) -> str:
    """Format CPU quota to human-readable string."""
    if quota is None:
        return "Unlimited"
    if quota >= 100:
        cores = quota / 100
        return f"{cores:.1f} core{'s' if cores != 1 else ''} ({quota}%)"
    return f"{quota}%"


@click.group()
def limits() -> None:
    """Resource limits management.

    Configure CPU, memory, and disk limits for projects using Linux cgroups
    via systemd resource controls.

    \b
    Default Limits:
      CPU quota     - 100% (1 core)
      Memory max    - 512MB (hard limit)
      Memory high   - 384MB (soft limit, throttle above)
      Tasks max     - 100 processes
      Disk quota    - 2048MB (advisory)

    \b
    Usage:
      hostkit limits show myapp
      hostkit limits set myapp --cpu 50 --memory 256M
      hostkit limits set myapp --unlimited
      hostkit limits reset myapp
    """
    pass


@limits.command("show")
@click.argument("project")
@click.pass_context
def show_limits(ctx: click.Context, project: str) -> None:
    """Show resource limits for a project.

    Displays configured limits and current disk usage.

    \b
    Examples:
      hostkit limits show myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = LimitsService()
        limits_config = service.get_limits(project)
        disk_status = service.check_disk_usage(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "limits": limits_config.to_dict() if limits_config else None,
                    "disk_usage": disk_status.to_dict(),
                    "defaults_applied": limits_config is None,
                },
                message="Resource limits retrieved",
            )
        else:
            click.echo(f"\nResource limits for {project}:\n")

            if limits_config is None:
                click.echo(click.style("No limits configured (defaults shown):", bold=True))
                from hostkit.services.limits_service import LimitsService as LS
                click.echo(f"  CPU quota:     {format_cpu(LS.DEFAULT_CPU_QUOTA)}")
                click.echo(f"  Memory max:    {format_size_mb(LS.DEFAULT_MEMORY_MAX_MB)}")
                click.echo(f"  Memory high:   {format_size_mb(LS.DEFAULT_MEMORY_HIGH_MB)}")
                click.echo(f"  Tasks max:     {LS.DEFAULT_TASKS_MAX}")
                click.echo(f"  Disk quota:    {format_size_mb(LS.DEFAULT_DISK_QUOTA_MB)}")
            else:
                enabled_color = "green" if limits_config.enabled else "yellow"
                enabled_text = "Enabled" if limits_config.enabled else "Disabled"
                click.echo(click.style("Configured Limits:", bold=True))
                click.echo(f"  Status:        {click.style(enabled_text, fg=enabled_color)}")
                click.echo(f"  CPU quota:     {format_cpu(limits_config.cpu_quota)}")
                click.echo(f"  Memory max:    {format_size_mb(limits_config.memory_max_mb)}")
                click.echo(f"  Memory high:   {format_size_mb(limits_config.memory_high_mb)}")
                click.echo(f"  Tasks max:     {limits_config.tasks_max or 'Unlimited'}")
                click.echo(f"  Disk quota:    {format_size_mb(limits_config.disk_quota_mb)}")

            click.echo()
            click.echo(click.style("Disk Usage:", bold=True))
            click.echo(f"  Home dir:      {disk_status.home_dir_mb}MB")
            click.echo(f"  Log dir:       {disk_status.log_dir_mb}MB")
            click.echo(f"  Total:         {disk_status.total_mb}MB")

            if disk_status.quota_mb:
                bar_width = 20
                used_pct = min(100, disk_status.percent_used or 0)
                filled = int((used_pct / 100) * bar_width)
                bar = "=" * filled + "-" * (bar_width - filled)

                color = "green" if used_pct < 80 else ("yellow" if used_pct < 95 else "red")
                click.echo(f"  Quota:         {disk_status.quota_mb}MB")
                click.echo(f"  Used:          [{click.style(bar, fg=color)}] {used_pct:.1f}%")

                if disk_status.over_quota:
                    click.echo()
                    click.echo(click.style("WARNING: Over disk quota!", fg="red", bold=True))

            click.echo()

    except LimitsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@limits.command("set")
@click.argument("project")
@click.option("--cpu", type=int, help="CPU quota as percentage (100 = 1 core)")
@click.option("--memory", "memory_str", help="Hard memory limit (e.g., 256M, 1G)")
@click.option("--memory-high", "memory_high_str", help="Soft memory limit / throttle threshold")
@click.option("--tasks", type=int, help="Max processes/threads")
@click.option("--disk", "disk_str", help="Disk quota (e.g., 1G, 2048M)")
@click.option("--enabled/--disabled", default=None, help="Enable or disable limits enforcement")
@click.option("--unlimited", is_flag=True, help="Clear all limits (unlimited)")
@click.option("--apply/--no-apply", default=True, help="Apply limits to running service")
@click.pass_context
@project_owner("project")
def set_limits(
    ctx: click.Context,
    project: str,
    cpu: int | None,
    memory_str: str | None,
    memory_high_str: str | None,
    tasks: int | None,
    disk_str: str | None,
    enabled: bool | None,
    unlimited: bool,
    apply: bool,
) -> None:
    """Set resource limits for a project.

    \b
    Examples:
      hostkit limits set myapp --cpu 50                    # 50% of 1 core
      hostkit limits set myapp --memory 512M              # 512MB hard limit
      hostkit limits set myapp --memory 1G --memory-high 768M
      hostkit limits set myapp --disk 2G                  # 2GB disk quota
      hostkit limits set myapp --tasks 50                 # Max 50 processes
      hostkit limits set myapp --cpu 100 --memory 1G --disk 5G  # Multiple
      hostkit limits set myapp --unlimited                # Remove all limits
      hostkit limits set myapp --disabled                 # Disable enforcement
    """
    formatter = get_formatter(ctx)

    # Parse size strings
    memory_max_mb = None
    memory_high_mb = None
    disk_quota_mb = None

    try:
        if memory_str:
            memory_max_mb = parse_size(memory_str)
        if memory_high_str:
            memory_high_mb = parse_size(memory_high_str)
        if disk_str:
            disk_quota_mb = parse_size(disk_str)
    except ValueError as e:
        formatter.error(
            code="INVALID_SIZE",
            message=f"Invalid size format: {e}",
            suggestion="Use format: 256M, 1G, or just a number for MB",
        )
        raise SystemExit(1)

    # Check that at least one option was provided
    if not unlimited and all(v is None for v in [cpu, memory_max_mb, memory_high_mb, tasks, disk_quota_mb, enabled]):
        formatter.error(
            code="NO_OPTIONS",
            message="No configuration options provided",
            suggestion="Use --cpu, --memory, --tasks, --disk, --enabled/--disabled, or --unlimited",
        )
        raise SystemExit(1)

    try:
        service = LimitsService()
        limits_config = service.set_limits(
            project_name=project,
            cpu_quota=cpu,
            memory_max_mb=memory_max_mb,
            memory_high_mb=memory_high_mb,
            tasks_max=tasks,
            disk_quota_mb=disk_quota_mb,
            enabled=enabled,
            unlimited=unlimited,
        )

        # Apply to running service if requested
        applied = False
        if apply and not unlimited:
            try:
                service.apply_limits(project)
                applied = True
            except LimitsServiceError:
                # Service might not be running, that's okay
                pass

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "limits": limits_config.to_dict(),
                    "applied": applied,
                },
                message="Resource limits updated",
            )
        else:
            if unlimited:
                click.echo(click.style("\nResource limits cleared (unlimited):", fg="green", bold=True))
            else:
                click.echo(click.style("\nResource limits updated:", fg="green", bold=True))

            status_color = "green" if limits_config.enabled else "yellow"
            status_text = "Enabled" if limits_config.enabled else "Disabled"
            click.echo(f"  Status:        {click.style(status_text, fg=status_color)}")
            click.echo(f"  CPU quota:     {format_cpu(limits_config.cpu_quota)}")
            click.echo(f"  Memory max:    {format_size_mb(limits_config.memory_max_mb)}")
            click.echo(f"  Memory high:   {format_size_mb(limits_config.memory_high_mb)}")
            click.echo(f"  Tasks max:     {limits_config.tasks_max or 'Unlimited'}")
            click.echo(f"  Disk quota:    {format_size_mb(limits_config.disk_quota_mb)}")

            if applied:
                click.echo()
                click.echo(click.style("Limits applied to running service", fg="green"))
            elif apply:
                click.echo()
                click.echo(click.style(
                    "Run 'hostkit limits apply' to apply to running service",
                    fg="yellow"
                ))

            click.echo()

    except LimitsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@limits.command("reset")
@click.argument("project")
@click.option("--apply/--no-apply", default=True, help="Apply limits to running service")
@click.pass_context
@project_owner("project")
def reset_limits(ctx: click.Context, project: str, apply: bool) -> None:
    """Reset resource limits to defaults.

    \b
    Examples:
      hostkit limits reset myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = LimitsService()
        limits_config = service.reset_limits(project)

        # Apply to running service if requested
        applied = False
        if apply:
            try:
                service.apply_limits(project)
                applied = True
            except LimitsServiceError:
                pass

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "limits": limits_config.to_dict(),
                    "applied": applied,
                },
                message="Resource limits reset to defaults",
            )
        else:
            click.echo(click.style("\nResource limits reset to defaults:", fg="green", bold=True))
            click.echo(f"  CPU quota:     {format_cpu(limits_config.cpu_quota)}")
            click.echo(f"  Memory max:    {format_size_mb(limits_config.memory_max_mb)}")
            click.echo(f"  Memory high:   {format_size_mb(limits_config.memory_high_mb)}")
            click.echo(f"  Tasks max:     {limits_config.tasks_max}")
            click.echo(f"  Disk quota:    {format_size_mb(limits_config.disk_quota_mb)}")

            if applied:
                click.echo()
                click.echo(click.style("Limits applied to running service", fg="green"))

            click.echo()

    except LimitsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@limits.command("apply")
@click.argument("project")
@click.pass_context
@project_owner("project")
def apply_limits(ctx: click.Context, project: str) -> None:
    """Apply resource limits to running systemd service.

    Updates the systemd service file and restarts the service to apply
    the configured limits.

    \b
    Examples:
      hostkit limits apply myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = LimitsService()
        result = service.apply_limits(project)

        if formatter.json_mode:
            formatter.success(
                data=result,
                message="Resource limits applied",
            )
        else:
            click.echo(click.style("\nResource limits applied to service:", fg="green", bold=True))
            click.echo(f"  Service: {result['service']}")

            if result["limits"]:
                limits_data = result["limits"]
                click.echo()
                click.echo(click.style("Active Limits:", bold=True))
                click.echo(f"  CPU quota:     {format_cpu(limits_data.get('cpu_quota'))}")
                click.echo(f"  Memory max:    {format_size_mb(limits_data.get('memory_max_mb'))}")
                click.echo(f"  Memory high:   {format_size_mb(limits_data.get('memory_high_mb'))}")
                click.echo(f"  Tasks max:     {limits_data.get('tasks_max') or 'Unlimited'}")
            else:
                click.echo()
                click.echo("No limits configured (unlimited)")

            click.echo()

    except LimitsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@limits.command("disk")
@click.argument("project")
@click.pass_context
def disk_usage(ctx: click.Context, project: str) -> None:
    """Show disk usage status for a project.

    \b
    Examples:
      hostkit limits disk myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = LimitsService()
        status = service.check_disk_usage(project)

        if formatter.json_mode:
            formatter.success(
                data=status.to_dict(),
                message="Disk usage retrieved",
            )
        else:
            click.echo(f"\nDisk usage for {project}:\n")

            click.echo(click.style("Usage:", bold=True))
            click.echo(f"  Home dir:  {status.home_dir_mb}MB")
            click.echo(f"  Log dir:   {status.log_dir_mb}MB")
            click.echo(f"  Total:     {status.total_mb}MB")

            if status.quota_mb:
                click.echo()
                click.echo(click.style("Quota:", bold=True))
                click.echo(f"  Limit:     {status.quota_mb}MB")

                bar_width = 30
                used_pct = min(100, status.percent_used or 0)
                filled = int((used_pct / 100) * bar_width)
                bar = "=" * filled + "-" * (bar_width - filled)

                color = "green" if used_pct < 80 else ("yellow" if used_pct < 95 else "red")
                click.echo(f"  Used:      [{click.style(bar, fg=color)}] {used_pct:.1f}%")

                if status.over_quota:
                    click.echo()
                    click.echo(click.style("WARNING: Over disk quota!", fg="red", bold=True))
                    click.echo("Consider cleaning up files or increasing the quota.")
            else:
                click.echo()
                click.echo("No disk quota configured")

            click.echo()

    except LimitsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

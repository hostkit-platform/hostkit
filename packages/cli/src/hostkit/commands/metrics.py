"""Metrics collection and querying commands for HostKit CLI."""

import click

from hostkit.output import OutputFormatter, format_bytes


@click.group("metrics")
def metrics() -> None:
    """Collect and view project metrics.

    Track CPU, memory, disk, requests, and error rates over time.
    """
    pass


@metrics.command("show")
@click.argument("project")
@click.option(
    "--since",
    "-s",
    default=None,
    help="Time range (e.g., 1h, 24h, 7d)",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=10,
    help="Number of samples to show",
)
@click.pass_context
def metrics_show(ctx: click.Context, project: str, since: str | None, limit: int) -> None:
    """Show metrics for a project.

    Displays current metrics and recent history.

    Examples:

        hostkit metrics show myapp
        hostkit metrics show myapp --since 1h
        hostkit metrics show myapp --since 24h --limit 50
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        service = MetricsService()
        config = service.get_config(project)

        # Get latest sample
        latest = service.get_latest(project)

        # Get summary
        summary_since = since or "1h"
        summary = service.get_summary(project, summary_since)

        # Get recent history
        history = service.get_history(project, since=since, limit=limit)

        if formatter.json_mode:
            formatter.success(
                {
                    "config": {
                        "enabled": config.enabled,
                        "collection_interval": config.collection_interval,
                        "retention_days": config.retention_days,
                        "last_collected_at": config.last_collected_at,
                    },
                    "latest": latest.to_dict() if latest else None,
                    "summary": summary.to_dict(),
                    "history_count": len(history),
                },
                f"Metrics for {project}",
            )
        else:
            # Build display sections
            sections = {}

            # Config section
            sections["configuration"] = {
                "enabled": "Yes" if config.enabled else "No",
                "collection_interval": f"{config.collection_interval}s",
                "retention_days": config.retention_days,
                "last_collected": (
                    config.last_collected_at[:19] if config.last_collected_at else "Never"
                ),
            }

            # Latest section
            if latest:
                sections["current"] = {
                    "cpu_percent": (
                        f"{latest.cpu_percent:.1f}%" if latest.cpu_percent is not None else "N/A"
                    ),
                    "memory": (
                        format_bytes(latest.memory_rss_bytes) if latest.memory_rss_bytes else "N/A"
                    ),
                    "memory_percent": (
                        f"{latest.memory_percent:.1f}%"
                        if latest.memory_percent is not None
                        else "N/A"
                    ),
                    "disk": (
                        format_bytes(latest.disk_used_bytes) if latest.disk_used_bytes else "N/A"
                    ),
                    "processes": latest.process_count or 0,
                }
            else:
                sections["current"] = {"status": "No data collected yet"}

            # Summary section
            sections[f"summary ({summary_since})"] = {
                "samples": summary.sample_count,
                "cpu_avg": f"{summary.cpu_avg:.1f}%" if summary.cpu_avg else "N/A",
                "cpu_max": f"{summary.cpu_max:.1f}%" if summary.cpu_max else "N/A",
                "memory_avg": f"{summary.memory_avg:.1f}%" if summary.memory_avg else "N/A",
                "memory_max": f"{summary.memory_max:.1f}%" if summary.memory_max else "N/A",
            }

            # Application metrics section
            if summary.total_requests:
                sections["application"] = {
                    "total_requests": summary.total_requests,
                    "2xx": summary.total_2xx,
                    "4xx": summary.total_4xx,
                    "5xx": summary.total_5xx,
                    "error_rate": f"{summary.error_rate:.1f}%" if summary.error_rate else "0%",
                    "avg_response": (
                        f"{summary.avg_response_ms:.1f}ms" if summary.avg_response_ms else "N/A"
                    ),
                    "p95_response": (
                        f"{summary.p95_response_ms:.1f}ms" if summary.p95_response_ms else "N/A"
                    ),
                }

            # Database section
            if summary.db_size_latest:
                sections["database"] = {
                    "size": format_bytes(summary.db_size_latest),
                    "connections_avg": (
                        f"{summary.db_connections_avg:.1f}" if summary.db_connections_avg else "N/A"
                    ),
                }

            formatter.status_panel(
                f"Metrics: {project}",
                sections,
                message=f"Metrics for {project}",
            )

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("history")
@click.argument("project")
@click.option(
    "--since",
    "-s",
    default="1h",
    help="Time range (e.g., 1h, 24h, 7d)",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=50,
    help="Maximum samples to return",
)
@click.pass_context
def metrics_history(ctx: click.Context, project: str, since: str, limit: int) -> None:
    """View historical metrics for a project.

    Examples:

        hostkit metrics history myapp
        hostkit metrics history myapp --since 24h
        hostkit metrics history myapp --since 7d --limit 100
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        service = MetricsService()
        history = service.get_history(project, since=since, limit=limit)

        if formatter.json_mode:
            formatter.success(
                {
                    "project": project,
                    "since": since,
                    "count": len(history),
                    "samples": [s.to_dict() for s in history],
                },
                f"Metrics history for {project}",
            )
        else:
            if not history:
                formatter.info(f"No metrics data found for '{project}' in the last {since}")
                return

            # Format as table
            rows = []
            for sample in history:
                rows.append(
                    {
                        "time": sample.collected_at[:19] if sample.collected_at else "",
                        "cpu": f"{sample.cpu_percent:.1f}%"
                        if sample.cpu_percent is not None
                        else "-",
                        "mem": (
                            f"{sample.memory_percent:.1f}%"
                            if sample.memory_percent is not None
                            else "-"
                        ),
                        "reqs": str(sample.requests_total) if sample.requests_total else "-",
                        "5xx": str(sample.requests_5xx) if sample.requests_5xx else "-",
                        "resp_ms": f"{sample.avg_response_ms:.0f}"
                        if sample.avg_response_ms
                        else "-",
                    }
                )

            formatter.table(
                rows,
                title=f"Metrics History: {project} (last {since})",
                columns=["time", "cpu", "mem", "reqs", "5xx", "resp_ms"],
            )

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("enable")
@click.argument("project")
@click.pass_context
def metrics_enable(ctx: click.Context, project: str) -> None:
    """Enable metrics collection for a project.

    Example:

        hostkit metrics enable myapp
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        service = MetricsService()
        config = service.enable_metrics(project)

        if formatter.json_mode:
            formatter.success(
                {
                    "project": project,
                    "enabled": config.enabled,
                    "collection_interval": config.collection_interval,
                    "retention_days": config.retention_days,
                },
                f"Metrics enabled for {project}",
            )
        else:
            formatter.info(f"Metrics collection enabled for '{project}'")
            formatter.info(f"Collection interval: {config.collection_interval}s")
            formatter.info(f"Retention: {config.retention_days} days")

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("disable")
@click.argument("project")
@click.pass_context
def metrics_disable(ctx: click.Context, project: str) -> None:
    """Disable metrics collection for a project.

    Example:

        hostkit metrics disable myapp
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        service = MetricsService()
        config = service.disable_metrics(project)

        if formatter.json_mode:
            formatter.success(
                {"project": project, "enabled": config.enabled},
                f"Metrics disabled for {project}",
            )
        else:
            formatter.info(f"Metrics collection disabled for '{project}'")

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("config")
@click.argument("project")
@click.option("--show", is_flag=True, help="Show current configuration")
@click.option("--interval", type=int, help="Collection interval in seconds")
@click.option("--retention", type=int, help="Retention period in days")
@click.option("--cpu-warning", type=float, help="CPU warning threshold (%)")
@click.option("--cpu-critical", type=float, help="CPU critical threshold (%)")
@click.option("--memory-warning", type=float, help="Memory warning threshold (%)")
@click.option("--memory-critical", type=float, help="Memory critical threshold (%)")
@click.option("--error-rate-warning", type=float, help="Error rate warning threshold (%)")
@click.option("--error-rate-critical", type=float, help="Error rate critical threshold (%)")
@click.pass_context
def metrics_config(
    ctx: click.Context,
    project: str,
    show: bool,
    interval: int | None,
    retention: int | None,
    cpu_warning: float | None,
    cpu_critical: float | None,
    memory_warning: float | None,
    memory_critical: float | None,
    error_rate_warning: float | None,
    error_rate_critical: float | None,
) -> None:
    """Configure metrics collection settings.

    Examples:

        hostkit metrics config myapp --show
        hostkit metrics config myapp --interval 30
        hostkit metrics config myapp --retention 14
        hostkit metrics config myapp --cpu-warning 75 --cpu-critical 90
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    try:
        service = MetricsService()

        # Check if any update options were provided
        has_updates = any(
            [
                interval is not None,
                retention is not None,
                cpu_warning is not None,
                cpu_critical is not None,
                memory_warning is not None,
                memory_critical is not None,
                error_rate_warning is not None,
                error_rate_critical is not None,
            ]
        )

        if has_updates:
            config = service.update_config(
                project,
                collection_interval=interval,
                retention_days=retention,
                cpu_warning=cpu_warning,
                cpu_critical=cpu_critical,
                memory_warning=memory_warning,
                memory_critical=memory_critical,
                error_rate_warning=error_rate_warning,
                error_rate_critical=error_rate_critical,
            )
            message = "Configuration updated"
        else:
            config = service.get_config(project)
            message = "Current configuration"

        if formatter.json_mode:
            formatter.success(
                {
                    "project": config.project_name,
                    "enabled": config.enabled,
                    "collection_interval": config.collection_interval,
                    "retention_days": config.retention_days,
                    "alert_on_threshold": config.alert_on_threshold,
                    "thresholds": {
                        "cpu_warning": config.cpu_warning_percent,
                        "cpu_critical": config.cpu_critical_percent,
                        "memory_warning": config.memory_warning_percent,
                        "memory_critical": config.memory_critical_percent,
                        "error_rate_warning": config.error_rate_warning_percent,
                        "error_rate_critical": config.error_rate_critical_percent,
                    },
                    "last_collected_at": config.last_collected_at,
                },
                message,
            )
        else:
            sections = {
                "general": {
                    "enabled": "Yes" if config.enabled else "No",
                    "collection_interval": f"{config.collection_interval}s",
                    "retention_days": config.retention_days,
                    "alert_on_threshold": ("Yes" if config.alert_on_threshold else "No"),
                    "last_collected": (
                        config.last_collected_at[:19] if config.last_collected_at else "Never"
                    ),
                },
                "thresholds": {
                    "cpu_warning": (
                        f"{config.cpu_warning_percent}%"
                        if config.cpu_warning_percent
                        else "Default (80%)"
                    ),
                    "cpu_critical": (
                        f"{config.cpu_critical_percent}%"
                        if config.cpu_critical_percent
                        else "Default (95%)"
                    ),
                    "memory_warning": (
                        f"{config.memory_warning_percent}%"
                        if config.memory_warning_percent
                        else "Default (80%)"
                    ),
                    "memory_critical": (
                        f"{config.memory_critical_percent}%"
                        if config.memory_critical_percent
                        else "Default (95%)"
                    ),
                    "error_rate_warning": (
                        f"{config.error_rate_warning_percent}%"
                        if config.error_rate_warning_percent
                        else "Default (5%)"
                    ),
                    "error_rate_critical": (
                        f"{config.error_rate_critical_percent}%"
                        if config.error_rate_critical_percent
                        else "Default (10%)"
                    ),
                },
            }
            formatter.status_panel(
                f"Metrics Config: {project}",
                sections,
                message=message,
            )

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("collect")
@click.argument("project", required=False)
@click.option("--all", "collect_all", is_flag=True, help="Collect metrics for all enabled projects")
@click.pass_context
def metrics_collect(ctx: click.Context, project: str | None, collect_all: bool) -> None:
    """Manually trigger metrics collection.

    Examples:

        hostkit metrics collect myapp
        hostkit metrics collect --all
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    if not project and not collect_all:
        formatter.error(
            code="MISSING_ARGUMENT",
            message="Either PROJECT or --all is required",
            suggestion="Use: hostkit metrics collect <project> or hostkit metrics collect --all",
        )

    try:
        service = MetricsService()

        if collect_all:
            samples = service.collect_all_metrics()
            if formatter.json_mode:
                formatter.success(
                    {
                        "projects_collected": len(samples),
                        "samples": [s.to_dict() for s in samples],
                    },
                    f"Collected metrics for {len(samples)} projects",
                )
            else:
                if samples:
                    formatter.info(f"Collected metrics for {len(samples)} projects")
                    for sample in samples:
                        cpu = (
                            f"{sample.cpu_percent:.1f}%"
                            if sample.cpu_percent is not None
                            else "N/A"
                        )
                        mem = (
                            f"{sample.memory_percent:.1f}%"
                            if sample.memory_percent is not None
                            else "N/A"
                        )
                        formatter.info(f"  {sample.project}: CPU {cpu}, Memory {mem}")
                else:
                    formatter.info("No projects with metrics enabled")
        else:
            sample = service.collect_metrics(project)
            if formatter.json_mode:
                formatter.success(sample.to_dict(), f"Collected metrics for {project}")
            else:
                formatter.info(f"Collected metrics for '{project}'")
                if sample.cpu_percent is not None:
                    formatter.info(f"  CPU: {sample.cpu_percent:.1f}%")
                if sample.memory_percent is not None:
                    formatter.info(f"  Memory: {sample.memory_percent:.1f}%")
                if sample.requests_total:
                    formatter.info(f"  Requests: {sample.requests_total}")

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@metrics.command("setup-timer")
@click.pass_context
def metrics_setup_timer(ctx: click.Context) -> None:
    """Setup systemd timer for automatic metrics collection.

    This creates and enables a systemd timer that runs metrics collection
    every 60 seconds for all projects with metrics enabled.

    Requires root access.

    Example:

        sudo hostkit metrics setup-timer
    """
    import os
    import shutil
    import subprocess
    from pathlib import Path

    formatter: OutputFormatter = ctx.obj["formatter"]

    if os.geteuid() != 0:
        formatter.error(
            code="ROOT_REQUIRED",
            message="Root access required to setup systemd timer",
            suggestion="Run: sudo hostkit metrics setup-timer",
        )

    # Find template directory
    template_dirs = [
        Path("/var/lib/hostkit/templates"),
        Path(__file__).parent.parent.parent.parent / "templates",
    ]

    template_dir = None
    for d in template_dirs:
        if d.exists():
            template_dir = d
            break

    if not template_dir:
        formatter.error(
            code="TEMPLATES_NOT_FOUND",
            message="Template directory not found",
            suggestion="Ensure HostKit is properly installed",
        )

    service_src = template_dir / "hostkit-metrics.service"
    timer_src = template_dir / "hostkit-metrics.timer"

    if not service_src.exists() or not timer_src.exists():
        formatter.error(
            code="TEMPLATES_NOT_FOUND",
            message="Metrics timer templates not found",
            suggestion=(
                "Check templates directory has hostkit-metrics.service and hostkit-metrics.timer"
            ),
        )

    # Copy to systemd directory
    systemd_dir = Path("/etc/systemd/system")
    service_dst = systemd_dir / "hostkit-metrics.service"
    timer_dst = systemd_dir / "hostkit-metrics.timer"

    try:
        shutil.copy(service_src, service_dst)
        shutil.copy(timer_src, timer_dst)

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True)

        # Enable and start the timer
        subprocess.run(["systemctl", "enable", "hostkit-metrics.timer"], check=True)
        subprocess.run(["systemctl", "start", "hostkit-metrics.timer"], check=True)

        if formatter.json_mode:
            formatter.success(
                {
                    "timer_enabled": True,
                    "service_path": str(service_dst),
                    "timer_path": str(timer_dst),
                },
                "Metrics timer setup complete",
            )
        else:
            formatter.info("Metrics collection timer installed and started")
            formatter.info("Timer runs every 60 seconds for all enabled projects")
            formatter.info("Check status: systemctl status hostkit-metrics.timer")

    except subprocess.CalledProcessError as e:
        formatter.error(
            code="SYSTEMD_ERROR",
            message=f"Failed to setup systemd timer: {e}",
            suggestion="Check systemd logs: journalctl -u hostkit-metrics",
        )
    except (OSError, PermissionError) as e:
        formatter.error(
            code="FILE_ERROR",
            message=f"Failed to copy timer files: {e}",
            suggestion="Ensure /etc/systemd/system is writable",
        )


@metrics.command("cleanup")
@click.argument("project", required=False)
@click.option("--all", "cleanup_all", is_flag=True, help="Cleanup metrics for all projects")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
def metrics_cleanup(
    ctx: click.Context,
    project: str | None,
    cleanup_all: bool,
    force: bool,
) -> None:
    """Delete old metrics data beyond retention period.

    Examples:

        hostkit metrics cleanup myapp --force
        hostkit metrics cleanup --all --force
    """
    from hostkit.services.metrics_service import MetricsService, MetricsServiceError

    formatter: OutputFormatter = ctx.obj["formatter"]

    if not project and not cleanup_all:
        formatter.error(
            code="MISSING_ARGUMENT",
            message="Either PROJECT or --all is required",
            suggestion="Use: hostkit metrics cleanup <project> or hostkit metrics cleanup --all",
        )

    if not force:
        formatter.error(
            code="CONFIRMATION_REQUIRED",
            message="This will delete old metrics data",
            suggestion="Add --force to confirm deletion",
        )

    try:
        service = MetricsService()
        deleted = service.cleanup_old_metrics(project if not cleanup_all else None)

        if formatter.json_mode:
            formatter.success(
                {"deleted_count": deleted},
                f"Deleted {deleted} old metrics records",
            )
        else:
            formatter.info(f"Deleted {deleted} old metrics records")

    except MetricsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)

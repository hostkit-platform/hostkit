"""Status command for HostKit CLI."""

import subprocess
import time
from typing import Any

import click
import psutil

from hostkit.database import get_db
from hostkit.output import OutputFormatter, format_bytes, format_uptime


def get_system_info() -> dict[str, Any]:
    """Get system resource information."""
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot_time = psutil.boot_time()
    uptime = psutil.time.time() - boot_time

    return {
        "cpu_usage": f"{cpu_percent}%",
        "memory_total": format_bytes(memory.total),
        "memory_used": format_bytes(memory.used),
        "memory_percent": f"{memory.percent}%",
        "disk_total": format_bytes(disk.total),
        "disk_used": format_bytes(disk.used),
        "disk_percent": f"{disk.percent}%",
        "uptime": format_uptime(uptime),
    }


def get_service_status(service_name: str) -> str:
    """Get systemd service status."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = result.stdout.strip()
        return status if status else "unknown"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


def get_services_status() -> dict[str, str]:
    """Get status of all managed services."""
    services = {
        "postgresql": "postgresql",
        "redis": "redis-server",
        "nginx": "nginx",
    }

    return {name: get_service_status(systemd_name) for name, systemd_name in services.items()}


def get_projects_summary() -> dict[str, Any]:
    """Get summary of managed projects."""
    db = get_db()
    projects = db.list_projects()

    if not projects:
        return {"total": 0, "running": 0, "stopped": 0, "projects": []}

    running = sum(1 for p in projects if p.get("status") == "running")
    stopped = len(projects) - running

    project_list = [
        {
            "name": p["name"],
            "runtime": p["runtime"],
            "port": p["port"],
            "status": p["status"],
        }
        for p in projects[:5]  # Show first 5 for summary
    ]

    return {
        "total": len(projects),
        "running": running,
        "stopped": stopped,
        "projects": project_list,
    }


@click.command("status")
@click.argument("project", required=False)
@click.option(
    "--resources",
    "-r",
    is_flag=True,
    help="Show detailed resource metrics (CPU, memory, disk, database)",
)
@click.option(
    "--vps",
    is_flag=True,
    help="Show VPS-level status (resources, limits, health)",
)
@click.option(
    "--watch",
    "-w",
    type=int,
    default=None,
    help="Continuously monitor resources every N seconds (requires --resources)",
)
@click.pass_context
def status(
    ctx: click.Context, project: str | None, resources: bool, vps: bool, watch: int | None
) -> None:
    """Show system status overview or project details.

    Without arguments, shows overall system status including services,
    resources, and project summary.

    With PROJECT argument, shows detailed status for that specific project.

    With --vps flag, shows VPS-level metrics (CPU, memory, disk, limits).

    Use --resources for detailed CPU, memory, disk, and database metrics.

    Examples:

        hostkit status                  # System overview
        hostkit status myapp            # Project details
        hostkit status myapp -r         # Project resources
        hostkit status myapp -r -w 30   # Watch resources every 30s
        hostkit status --vps            # VPS metrics
        hostkit --json status --vps     # VPS metrics as JSON
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    # --vps mode takes priority
    if vps:
        _show_vps_status(formatter)
        return

    # --watch requires --resources and a project
    if watch is not None:
        if not resources:
            formatter.error(
                code="INVALID_OPTIONS",
                message="--watch requires --resources flag",
                suggestion="Use: hostkit status <project> --resources --watch 30",
            )
        if not project:
            formatter.error(
                code="PROJECT_REQUIRED",
                message="--watch requires a project name",
                suggestion="Use: hostkit status <project> --resources --watch 30",
            )

    if project:
        if resources:
            # Show resource metrics (with optional watch)
            _show_project_resources(formatter, project, watch)
        else:
            # Show project-specific status
            _show_project_status(formatter, project)
    else:
        # Show system overview
        _show_system_status(formatter)


def _show_system_status(formatter: OutputFormatter) -> None:
    """Display overall system status."""
    system_info = get_system_info()
    services = get_services_status()
    projects = get_projects_summary()

    data = {
        "system": system_info,
        "services": services,
        "projects": projects,
    }

    formatter.status_panel("HostKit Status", data, message="System status retrieved")


def _show_vps_status(formatter: OutputFormatter) -> None:
    """Display VPS-level status with resources, limits, and health."""
    from hostkit.config import get_config

    config = get_config()

    # Get CPU percent
    cpu_percent = psutil.cpu_percent(interval=0.5)

    # Get memory info
    memory = psutil.virtual_memory()
    memory_used_gb = memory.used / (1024**3)
    memory_total_gb = memory.total / (1024**3)

    # Get disk info
    disk = psutil.disk_usage("/")
    disk_used_gb = disk.used / (1024**3)
    disk_total_gb = disk.total / (1024**3)

    # Get project count
    db = get_db()
    projects = db.list_projects()
    project_count = len(projects)
    max_projects = config.max_projects  # Default 50

    # Get Redis DB usage
    redis_dbs_used = len([p for p in projects if p.get("redis_db") is not None])
    max_redis_dbs = 50  # Hardcoded limit

    # Check core service health
    services = get_services_status()
    all_healthy = all(s == "active" for s in services.values())

    # Build response
    data = {
        "resources": {
            "cpu_percent": round(cpu_percent, 1),
            "memory_gb": f"{memory_used_gb:.1f}/{memory_total_gb:.1f}",
            "disk_gb": f"{disk_used_gb:.0f}/{disk_total_gb:.0f}",
        },
        "limits": {
            "projects": f"{project_count}/{max_projects}",
            "redis_dbs": f"{redis_dbs_used}/{max_redis_dbs}",
        },
        "healthy": all_healthy,
    }

    # For non-JSON mode, add more details
    if not formatter.json_mode:
        data["services"] = services
        uptime = psutil.time.time() - psutil.boot_time()
        data["uptime"] = format_uptime(uptime)

    formatter.success(data, "VPS status retrieved")


def _show_project_status(formatter: OutputFormatter, project_name: str) -> None:
    """Display status for a specific project."""
    db = get_db()
    project = db.get_project(project_name)

    if not project:
        formatter.error(
            code="PROJECT_NOT_FOUND",
            message=f"Project '{project_name}' does not exist",
            suggestion="Run 'hostkit project list' to see available projects",
        )

    # Get project's domains
    domains = db.list_domains(project_name)

    # Get service status if project has a systemd service
    service_name = f"hostkit-{project_name}"
    service_status = get_service_status(service_name)

    # Get log statistics
    log_stats = _get_project_log_stats(project_name)

    # Get secrets status
    secrets_status = _get_project_secrets_status(project_name)

    # Get auto-pause status
    pause_status = _get_project_pause_status(project_name)

    data = {
        "project": {
            "name": project["name"],
            "runtime": project["runtime"],
            "port": project["port"],
            "redis_db": project.get("redis_db"),
            "status": project["status"],
            "created_at": project["created_at"],
        },
        "service": {
            "name": service_name,
            "status": service_status,
        },
        "auto_pause": pause_status,
        "secrets": secrets_status,
        "logs": log_stats,
        "domains": [
            {
                "domain": d["domain"],
                "ssl": "yes" if d["ssl_provisioned"] else "no",
            }
            for d in domains
        ],
    }

    formatter.status_panel(
        f"Project: {project_name}",
        data,
        message=f"Status for project '{project_name}' retrieved",
    )


def _get_project_log_stats(project_name: str) -> dict[str, Any]:
    """Get log statistics for a project."""
    try:
        from hostkit.services.log_service import LogService
        log_service = LogService()
        stats = log_service.get_log_stats(project_name)
        return {
            "total_size": format_bytes(stats.total_size),
            "file_count": stats.file_count,
            "errors_24h": stats.error_count_24h,
            "warnings_24h": stats.warning_count_24h,
            "last_activity": stats.newest_entry[:19] if stats.newest_entry else "N/A",
        }
    except Exception:
        return {
            "total_size": "N/A",
            "file_count": 0,
            "errors_24h": 0,
            "warnings_24h": 0,
            "last_activity": "N/A",
        }


def _get_project_secrets_status(project_name: str) -> dict[str, Any]:
    """Get secrets status for a project."""
    try:
        from hostkit.services.secrets_service import SecretsService, SecretsServiceError

        secrets_service = SecretsService()

        # Get verification results
        try:
            verify_result = secrets_service.verify_secrets(project_name)
            return {
                "configured": True,
                "required_count": verify_result.get("required_count", 0),
                "required_set": verify_result.get("required_set", 0),
                "optional_count": verify_result.get("optional_count", 0),
                "optional_set": verify_result.get("optional_set", 0),
                "ready": verify_result.get("ready", False),
                "has_warnings": verify_result.get("has_warnings", False),
            }
        except SecretsServiceError:
            return {
                "configured": False,
                "required_count": 0,
                "required_set": 0,
                "optional_count": 0,
                "optional_set": 0,
                "ready": True,
                "has_warnings": False,
            }
    except Exception:
        return {
            "configured": False,
            "required_count": 0,
            "required_set": 0,
            "optional_count": 0,
            "optional_set": 0,
            "ready": True,
            "has_warnings": False,
        }


def _get_project_pause_status(project_name: str) -> dict[str, Any]:
    """Get auto-pause status for a project."""
    try:
        from hostkit.services.auto_pause_service import AutoPauseService

        service = AutoPauseService()
        config = service.get_config(project_name)

        return {
            "enabled": config.enabled,
            "paused": config.paused,
            "paused_at": config.paused_at,
            "paused_reason": config.paused_reason,
            "failure_threshold": config.failure_threshold,
            "window_minutes": config.window_minutes,
        }
    except Exception:
        return {
            "enabled": False,
            "paused": False,
            "paused_at": None,
            "paused_reason": None,
            "failure_threshold": 5,
            "window_minutes": 10,
        }


def _show_project_resources(
    formatter: OutputFormatter, project_name: str, watch_interval: int | None = None
) -> None:
    """Display detailed resource metrics for a project."""
    from hostkit.services.resource_service import ResourceService, ResourceServiceError

    try:
        service = ResourceService()

        if watch_interval is not None:
            # Continuous monitoring mode
            _watch_project_resources(formatter, service, project_name, watch_interval)
        else:
            # Single snapshot
            resources = service.get_project_resources(project_name)
            _display_resources(formatter, resources)

    except ResourceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


def _watch_project_resources(
    formatter: OutputFormatter,
    service: "ResourceService",
    project_name: str,
    interval: int,
) -> None:
    """Continuously monitor and display resource metrics."""
    from hostkit.services.resource_service import ResourceService

    import click

    click.echo(f"Monitoring {project_name} every {interval}s (Ctrl+C to stop)\n")

    try:
        for resources in service.watch_resources(project_name, interval=interval):
            # Clear screen for fresh display
            click.clear()
            click.echo(f"Monitoring {project_name} every {interval}s (Ctrl+C to stop)\n")
            _display_resources(formatter, resources)
    except KeyboardInterrupt:
        click.echo("\nMonitoring stopped.")


def _display_resources(formatter: OutputFormatter, resources: "ProjectResources") -> None:
    """Display resource metrics in appropriate format."""
    from hostkit.services.resource_service import ProjectResources

    if formatter.json_mode:
        formatter.success(resources.to_dict(), f"Resources for {resources.project}")
        return

    # Build sections for status_panel
    sections: dict[str, Any] = {}

    # Process section
    process_status = "running" if resources.process_count > 0 else "stopped"
    sections["process"] = {
        "status": process_status,
        "pid": resources.main_pid or "N/A",
        "process_count": resources.process_count,
        "cpu_percent": f"{resources.cpu_percent:.1f}%" if resources.cpu_percent is not None else "N/A",
        "memory_rss": format_bytes(resources.memory_rss_bytes) if resources.memory_rss_bytes else "N/A",
        "memory_vms": format_bytes(resources.memory_vms_bytes) if resources.memory_vms_bytes else "N/A",
        "memory_percent": f"{resources.memory_percent:.1f}%" if resources.memory_percent is not None else "N/A",
    }

    # Disk section
    sections["disk"] = {
        "home_directory": format_bytes(resources.home_dir_bytes),
        "log_directory": format_bytes(resources.log_dir_bytes),
        "backup_directory": format_bytes(resources.backup_dir_bytes),
        "total": format_bytes(resources.total_disk_bytes),
    }

    # Database section (if present)
    if resources.database_name:
        sections["database"] = {
            "name": resources.database_name,
            "size": format_bytes(resources.database_size_bytes) if resources.database_size_bytes else "N/A",
            "connections": resources.database_connections if resources.database_connections is not None else "N/A",
        }

    # Alerts section (if any)
    if resources.alerts:
        alert_items = []
        for alert in resources.alerts:
            level = alert["level"]
            message = alert["message"]
            if level == "critical":
                alert_items.append({"level": "critical", "message": message})
            else:
                alert_items.append({"level": "warning", "message": message})
        sections["alerts"] = alert_items
    else:
        sections["alerts"] = [{"level": "ok", "message": "No alerts"}]

    # Timestamp
    sections["metadata"] = {
        "timestamp": resources.timestamp,
    }

    formatter.status_panel(f"Resources: {resources.project}", sections, message=f"Resources for {resources.project}")

"""Booking service management commands for HostKit."""

import click

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.booking_service import BookingService, BookingServiceError


@click.group()
@click.pass_context
def booking(ctx: click.Context) -> None:
    """Manage per-project booking services.

    Enable appointment scheduling with provider pooling, room management,
    and automated reminders.
    """
    pass


@booking.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def booking_enable(ctx: click.Context, project: str) -> None:
    """Enable booking service for a project.

    Creates database tables and deploys the booking FastAPI service.

    Example:
        hostkit booking enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        result = service.enable_booking(project=project)

        formatter.success(
            message=f"Booking service enabled for '{project}'",
            data={
                "project": project,
                "booking_port": result["booking_port"],
                "api_url": result["api_url"],
                "admin_url": result["admin_url"],
                "docs_url": result["docs_url"],
                "status": "active",
                "next_steps": "Configure providers and services via the API",
            },
        )

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@booking.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def booking_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable booking service for a project.

    Removes the booking service and configuration.
    Requires --force to confirm.

    WARNING: This will delete all booking data!

    Example:
        hostkit booking disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        service.disable_booking(project=project, force=force)

        formatter.success(
            message=f"Booking service disabled for '{project}'",
            data={
                "project": project,
                "booking_tables_dropped": True,
            },
        )

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@booking.command("status")
@click.argument("project")
@click.pass_context
@project_access("project")
def booking_status(ctx: click.Context, project: str) -> None:
    """Show booking service status for a project.

    Displays service status, URLs, and statistics.

    Example:
        hostkit booking status myapp
        hostkit --json booking status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        status = service.get_booking_status(project=project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Booking status for '{project}'",
                data=status,
            )
        else:
            click.echo(f"\nBooking Status: {project}")
            click.echo("-" * 60)

            if not status["enabled"]:
                click.echo("  Status: DISABLED")
                click.echo(f"\n  Enable with: hostkit booking enable {project}")
            else:
                click.echo("  Status: ACTIVE")
                click.echo(f"  Booking Port: {status['booking_port']}")
                click.echo(f"  API URL: {status['api_url']}")
                click.echo(f"  Admin API: {status['admin_url']}")
                click.echo(f"  API Docs: {status['docs_url']}")
                click.echo(f"  Providers: {status['provider_count']}")
                click.echo(f"  Services: {status['service_count']}")
                click.echo(f"  Appointments: {status['appointment_count']}")

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@booking.command("seed")
@click.argument("project")
@click.option("--providers", default=3, help="Number of providers to create (default: 3)")
@click.option("--services", default=5, help="Number of services to create (default: 5)")
@click.pass_context
@project_owner("project")
def booking_seed(ctx: click.Context, project: str, providers: int, services: int) -> None:
    """Seed demo data for testing.

    Creates sample providers, services, and rooms for development/testing.

    Example:
        hostkit booking seed myapp
        hostkit booking seed myapp --providers 5 --services 10
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        result = service.seed_demo_data(
            project=project,
            provider_count=providers,
            service_count=services,
        )

        formatter.success(
            message=f"Demo data seeded for '{project}'",
            data=result,
        )

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@booking.command("upgrade")
@click.argument("project")
@click.option("--dry-run", is_flag=True, help="Preview changes without applying")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
@project_owner("project")
def booking_upgrade(ctx: click.Context, project: str, dry_run: bool, force: bool) -> None:
    """Upgrade booking service to latest version.

    Syncs code from templates, applies schema migrations, and restarts service.
    All existing data (providers, services, appointments) is preserved.

    Example:
        hostkit booking upgrade myapp --dry-run
        hostkit booking upgrade myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        preview = service.upgrade_booking(project=project, dry_run=True)
        has_changes = preview["files_copied"] > 0 or len(preview["migrations"]) > 0

        if not has_changes:
            formatter.success(
                message=f"Booking service for '{project}' is already up to date",
                data={
                    "project": project,
                    "current_version": preview["current_version"],
                    "status": "up_to_date",
                },
            )
            return

        if dry_run:
            if ctx.obj["json_mode"]:
                formatter.success(message=f"Booking upgrade preview for '{project}'", data=preview)
            else:
                click.echo(f"\nBooking upgrade preview for '{project}':")
                click.echo("-" * 60)
                click.echo(f"  Current version: {preview['current_version']}")
                click.echo(f"  Target version:  {preview['target_version']}")
                if preview["file_changes"]:
                    click.echo("\n  Code changes:")
                    for fc in preview["file_changes"]:
                        click.echo(f"    - {fc['path']} ({fc['action']})")
                if preview["migrations"]:
                    click.echo("\n  Schema changes:")
                    for m in preview["migrations"]:
                        click.echo(f"    - Migration {m['version']}: {m['description']}")
                preserved = preview["preserved_data"]
                click.echo(
                    f"\n  Data preserved:"
                    f" {preserved['providers']} providers,"
                    f" {preserved['services']} services,"
                    f" {preserved['appointments']} appointments"
                )
                click.echo("\nRun without --dry-run to apply changes.")
            return

        if not force and not ctx.obj["json_mode"]:
            click.echo(f"\nAbout to upgrade booking service for '{project}':")
            click.echo(f"  - {preview['files_copied']} files will be updated")
            click.echo(f"  - {len(preview['migrations'])} migrations will be applied")
            if not click.confirm("\nProceed with upgrade?"):
                click.echo("Upgrade cancelled.")
                return

        result = service.upgrade_booking(project=project, dry_run=False)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Booking service upgraded for '{project}'", data=result)
        else:
            click.echo(f"\nUpgrading booking service for '{project}'...")
            copied_icon = "✓" if result["files_copied"] > 0 else "○"
            migrated_icon = "✓" if result["migrations_applied"] > 0 else "○"
            restart_icon = "✓" if result["restarted"] else "✗"
            health_icon = "✓" if result["healthy"] else "✗"
            health_text = "passed" if result["healthy"] else "failed"
            click.echo(f"  {copied_icon} Copied {result['files_copied']} files")
            click.echo(f"  {migrated_icon} Applied {result['migrations_applied']} migrations")
            click.echo(f"  {restart_icon} Restarted service")
            click.echo(f"  {health_icon} Health check {health_text}")
            click.echo(f"\nBooking service upgraded to v{result['target_version']}")

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@booking.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def booking_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View booking service logs for a project.

    Shows logs from the project's booking service.
    Use --follow to stream logs in real-time.

    Example:
        hostkit booking logs myapp
        hostkit booking logs myapp --lines 50
        hostkit booking logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = BookingService()

    try:
        # Verify booking is enabled for project
        if not service.booking_is_enabled(project):
            raise BookingServiceError(
                code="BOOKING_NOT_ENABLED",
                message=f"Booking service is not enabled for '{project}'",
                suggestion=f"Enable booking first with 'hostkit booking enable {project}'",
            )

        service_name = f"hostkit-{project}-booking"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following booking logs for {project}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            proc = subprocess.Popen(
                ["journalctl", "-u", f"{service_name}.service", "-f", "-n", str(lines)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            try:
                for line in iter(proc.stdout.readline, b""):
                    sys.stdout.write(line.decode())
                    sys.stdout.flush()
            except KeyboardInterrupt:
                proc.terminate()
                click.echo("\n--- Log stream ended ---")
        else:
            result = subprocess.run(
                ["journalctl", "-u", f"{service_name}.service", "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
            )

            logs = result.stdout

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Booking logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nBooking logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except BookingServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

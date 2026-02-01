"""Backup management CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.backup_service import BackupService, BackupServiceError


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


@click.group()
def backup() -> None:
    """Backup management commands.

    Create, list, restore, and manage backups for HostKit projects.

    \b
    Backup Types:
      full        - Database + files + environment
      db          - Database only
      files       - Application files only
      credentials - Environment file only

    \b
    Retention Policy:
      - 7 daily backups
      - 4 weekly backups (Mondays)
    """
    pass


@backup.command("list")
@click.argument("project", required=False)
@click.option("--all", "show_all", is_flag=True, help="Show all backups across projects")
@click.option("--r2", "show_r2", is_flag=True, help="Show R2 cloud sync status")
@click.pass_context
def list_backups(ctx: click.Context, project: str | None, show_all: bool, show_r2: bool) -> None:
    """List backups for a project or all projects.

    \b
    Examples:
      hostkit backup list myapp        List backups for myapp
      hostkit backup list --all        List all backups
      hostkit backup list myapp --r2   Show R2 sync status
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if show_all:
            project = None

        backups = service.list_backups(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "backups": [
                        {
                            "id": b.id,
                            "project": b.project,
                            "type": b.backup_type,
                            "path": b.path,
                            "size_bytes": b.size_bytes,
                            "created_at": b.created_at,
                            "is_weekly": b.is_weekly,
                            "r2_synced": b.r2_synced,
                            "r2_key": b.r2_key,
                            "r2_synced_at": b.r2_synced_at,
                            "local_exists": b.local_exists,
                        }
                        for b in backups
                    ]
                },
                message=f"Found {len(backups)} backup(s)",
            )
        else:
            if not backups:
                click.echo("No backups found.")
                return

            click.echo(f"\nBackups ({len(backups)} total):\n")

            # Header - include R2 column if requested
            if show_r2:
                click.echo(f"{'ID':<45} {'Type':<8} {'Size':<10} {'Created':<20} {'R2':<5} {'Local'}")
                click.echo("-" * 105)
            else:
                click.echo(f"{'ID':<45} {'Type':<8} {'Size':<10} {'Created':<20} {'Weekly'}")
                click.echo("-" * 100)

            current_project = None
            for b in backups:
                if b.project != current_project:
                    if current_project is not None:
                        click.echo("")
                    click.echo(click.style(f"Project: {b.project}", fg="cyan", bold=True))
                    current_project = b.project

                size_str = format_size(b.size_bytes)
                created = b.created_at[:19].replace("T", " ")

                if show_r2:
                    r2_marker = click.style("[R2]", fg="blue") if b.r2_synced else ""
                    local_marker = "[L]" if b.local_exists else click.style("[R2 only]", fg="yellow")
                    click.echo(f"  {b.id:<43} {b.backup_type:<8} {size_str:<10} {created:<20} {r2_marker:<5} {local_marker}")
                else:
                    weekly_marker = "[W]" if b.is_weekly else ""
                    click.echo(f"  {b.id:<43} {b.backup_type:<8} {size_str:<10} {created:<20} {weekly_marker}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("create")
@click.argument("project")
@click.option("--type", "backup_type", default="full", type=click.Choice(["full", "db", "files", "credentials"]), help="Backup type (default: full)")
@click.option("--full", "full_backup", is_flag=True, help="Create full backup (shorthand)")
@click.option("--r2", "upload_r2", is_flag=True, help="Also upload to R2 cloud storage")
@click.pass_context
@project_owner("project")
def create_backup(ctx: click.Context, project: str, backup_type: str, full_backup: bool, upload_r2: bool) -> None:
    """Create a backup for a project.

    \b
    Backup Types:
      full        - Database + files + environment (default)
      db          - Database only
      files       - Application files only
      credentials - Environment file only

    \b
    Examples:
      hostkit backup create myapp              Create full backup
      hostkit backup create myapp --type db    Create database backup
      hostkit backup create myapp --full       Create full backup
      hostkit backup create myapp --r2         Create and upload to R2
    """
    formatter = get_formatter(ctx)

    if full_backup:
        backup_type = "full"

    try:
        service = BackupService()

        if not formatter.json_mode:
            msg = f"Creating {backup_type} backup for {project}..."
            if upload_r2:
                msg += " (with R2 sync)"
            click.echo(msg)

        backup = service.create_backup(project, backup_type, upload_to_r2=upload_r2)

        if formatter.json_mode:
            formatter.success(
                data={
                    "backup_id": backup.id,
                    "project": backup.project,
                    "type": backup.backup_type,
                    "path": backup.path,
                    "size_bytes": backup.size_bytes,
                    "created_at": backup.created_at,
                    "r2_synced": backup.r2_synced,
                    "r2_key": backup.r2_key,
                    "r2_synced_at": backup.r2_synced_at,
                },
                message="Backup created successfully",
            )
        else:
            click.echo(click.style("\n✓ Backup created successfully\n", fg="green", bold=True))
            click.echo(f"  ID:      {backup.id}")
            click.echo(f"  Type:    {backup.backup_type}")
            click.echo(f"  Size:    {format_size(backup.size_bytes)}")
            click.echo(f"  Path:    {backup.path}")
            click.echo(f"  Created: {backup.created_at}")
            if backup.r2_synced:
                click.echo(click.style(f"  R2:      Synced to {backup.r2_key}", fg="blue"))
            elif upload_r2:
                click.echo(click.style("  R2:      Sync failed (local backup still valid)", fg="yellow"))

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("restore")
@click.argument("project")
@click.argument("backup_id")
@click.option("--db/--no-db", "restore_db", default=True, help="Restore database (default: yes)")
@click.option("--files/--no-files", "restore_files", default=True, help="Restore files (default: yes)")
@click.option("--env", "restore_env", is_flag=True, help="Restore environment file (default: no)")
@click.option("--from-r2", "from_r2", is_flag=True, help="Download from R2 if local file missing")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
@project_owner("project")
def restore_backup(
    ctx: click.Context,
    project: str,
    backup_id: str,
    restore_db: bool,
    restore_files: bool,
    restore_env: bool,
    from_r2: bool,
    force: bool,
) -> None:
    """Restore a project from backup.

    WARNING: This will stop the service, overwrite existing data, and restart.
    Database and files are restored by default. Environment is NOT restored
    unless explicitly requested with --env.

    Use --from-r2 to download the backup from R2 cloud storage if the local
    file has been deleted.

    \b
    Examples:
      hostkit backup restore myapp myapp_full_20250101_120000
      hostkit backup restore myapp myapp_db_20250101_120000 --no-files
      hostkit backup restore myapp myapp_full_20250101_120000 --env --force
      hostkit backup restore myapp myapp_full_20250101_120000 --from-r2
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        # Get backup info first
        backup = service.get_backup(backup_id)
        if not backup:
            formatter.error(
                code="BACKUP_NOT_FOUND",
                message=f"Backup '{backup_id}' not found",
                suggestion="Run 'hostkit backup list' to see available backups",
            )
            raise SystemExit(1)

        # Confirm if not forced
        if not force and not formatter.json_mode:
            click.echo(f"\nAbout to restore backup: {backup_id}")
            click.echo(f"  Project:    {project}")
            click.echo(f"  Type:       {backup.backup_type}")
            click.echo(f"  Created:    {backup.created_at}")
            click.echo(f"  Local:      {'Yes' if backup.local_exists else 'No'}")
            if backup.r2_synced:
                click.echo(f"  R2:         {backup.r2_key}")
            click.echo(f"  Restore DB: {'Yes' if restore_db else 'No'}")
            click.echo(f"  Restore Files: {'Yes' if restore_files else 'No'}")
            click.echo(f"  Restore Env: {'Yes' if restore_env else 'No'}")
            if from_r2 and not backup.local_exists:
                click.echo(click.style("  Will download from R2 first", fg="blue"))
            click.echo("")
            click.echo(click.style("WARNING: This will overwrite existing data!", fg="yellow", bold=True))

            if not click.confirm("Do you want to proceed?"):
                click.echo("Restore cancelled.")
                return

        if not formatter.json_mode:
            if from_r2 and not backup.local_exists:
                click.echo(f"\nDownloading backup from R2...")
            click.echo(f"Restoring backup for {project}...")

        result = service.restore_backup(
            project=project,
            backup_id=backup_id,
            restore_db=restore_db,
            restore_files=restore_files,
            restore_env=restore_env,
            from_r2=from_r2,
        )

        if formatter.json_mode:
            formatter.success(data=result, message="Backup restored successfully")
        else:
            click.echo(click.style("\n✓ Backup restored successfully\n", fg="green", bold=True))
            click.echo(f"  Database restored: {'Yes' if result['restored']['database'] else 'No'}")
            click.echo(f"  Files restored:    {'Yes' if result['restored']['files'] else 'No'}")
            click.echo(f"  Env restored:      {'Yes' if result['restored']['env'] else 'No'}")
            click.echo(f"\nService has been restarted.")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("verify")
@click.argument("backup_id")
@click.pass_context
def verify_backup(ctx: click.Context, backup_id: str) -> None:
    """Verify backup integrity.

    Checks that the backup file exists, can be decompressed, and contains
    the expected components.

    \b
    Examples:
      hostkit backup verify myapp_full_20250101_120000
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if not formatter.json_mode:
            click.echo(f"Verifying backup: {backup_id}...")

        result = service.verify_backup(backup_id)

        if formatter.json_mode:
            formatter.success(
                data={
                    "backup_id": result.backup_id,
                    "valid": result.valid,
                    "checks": result.checks,
                    "errors": result.errors,
                },
                message="Verification complete",
            )
        else:
            click.echo("")
            if result.valid:
                click.echo(click.style("✓ Backup is valid\n", fg="green", bold=True))
            else:
                click.echo(click.style("✗ Backup verification failed\n", fg="red", bold=True))

            click.echo("Checks:")
            for check, passed in result.checks.items():
                status = click.style("✓", fg="green") if passed else click.style("✗", fg="red")
                click.echo(f"  {status} {check.replace('_', ' ').title()}")

            if result.errors:
                click.echo("\nErrors:")
                for error in result.errors:
                    click.echo(f"  - {error}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("delete")
@click.argument("backup_id")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def delete_backup(ctx: click.Context, backup_id: str, force: bool) -> None:
    """Delete a backup.

    \b
    Examples:
      hostkit backup delete myapp_full_20250101_120000 --force
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        # Get backup info first
        backup = service.get_backup(backup_id)
        if not backup:
            formatter.error(
                code="BACKUP_NOT_FOUND",
                message=f"Backup '{backup_id}' not found",
                suggestion="Run 'hostkit backup list' to see available backups",
            )
            raise SystemExit(1)

        # Confirm if not forced
        if not force and not formatter.json_mode:
            click.echo(f"\nAbout to delete backup: {backup_id}")
            click.echo(f"  Project: {backup.project}")
            click.echo(f"  Size:    {format_size(backup.size_bytes)}")
            click.echo(f"  Created: {backup.created_at}")

            if not click.confirm("\nDo you want to delete this backup?"):
                click.echo("Delete cancelled.")
                return

        deleted = service.delete_backup(backup_id)

        if formatter.json_mode:
            formatter.success(
                data={"backup_id": backup_id, "deleted": deleted},
                message="Backup deleted" if deleted else "Backup not found",
            )
        else:
            if deleted:
                click.echo(click.style(f"✓ Backup {backup_id} deleted", fg="green"))
            else:
                click.echo(click.style(f"Backup {backup_id} not found", fg="yellow"))

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("rotate")
@click.argument("project", required=False)
@click.option("--all", "rotate_all", is_flag=True, help="Rotate backups for all projects")
@click.pass_context
def rotate_backups(ctx: click.Context, project: str | None, rotate_all: bool) -> None:
    """Apply backup retention policy.

    Keeps 7 daily backups and 4 weekly backups (Mondays).
    Older backups are deleted.

    \b
    Examples:
      hostkit backup rotate myapp     Rotate backups for myapp
      hostkit backup rotate --all     Rotate all project backups
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if rotate_all:
            if not formatter.json_mode:
                click.echo("Rotating backups for all projects...")

            results = service.rotate_all_backups()

            if formatter.json_mode:
                formatter.success(data=results, message="Rotation complete")
            else:
                total_deleted = sum(r["deleted_count"] for r in results.values())
                click.echo(click.style(f"\n✓ Rotation complete\n", fg="green", bold=True))
                click.echo(f"Projects processed: {len(results)}")
                click.echo(f"Total backups deleted: {total_deleted}")

        elif project:
            if not formatter.json_mode:
                click.echo(f"Rotating backups for {project}...")

            result = service.rotate_backups(project)

            if formatter.json_mode:
                formatter.success(data=result, message="Rotation complete")
            else:
                click.echo(click.style("\n✓ Rotation complete\n", fg="green", bold=True))
                click.echo(f"  Deleted: {result['deleted_count']} backup(s)")
                click.echo(f"  Kept daily: {result['kept_daily']}")
                click.echo(f"  Kept weekly: {result['kept_weekly']}")
                click.echo(f"  Total remaining: {result['total_remaining']}")

        else:
            formatter.error(
                code="MISSING_ARGUMENT",
                message="Please specify a project or use --all",
                suggestion="hostkit backup rotate myapp OR hostkit backup rotate --all",
            )
            raise SystemExit(1)

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("export")
@click.argument("backup_id")
@click.argument("destination")
@click.pass_context
def export_backup(ctx: click.Context, backup_id: str, destination: str) -> None:
    """Export/copy a backup to a destination path.

    Useful for downloading backups via SCP or storing in external location.

    \b
    Examples:
      hostkit backup export myapp_full_20250101_120000 /tmp/myapp-backup.tar.gz
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if not formatter.json_mode:
            click.echo(f"Exporting backup {backup_id}...")

        result = service.export_backup(backup_id, destination)

        if formatter.json_mode:
            formatter.success(data=result, message="Backup exported successfully")
        else:
            click.echo(click.style("\n✓ Backup exported successfully\n", fg="green", bold=True))
            click.echo(f"  Destination: {result['destination']}")
            click.echo(f"  Size:        {format_size(result['size_bytes'])}")
            click.echo(f"\nYou can download this file via SCP:")
            click.echo(f"  scp root@your-vps:{result['destination']} ./")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("stats")
@click.argument("project", required=False)
@click.pass_context
def backup_stats(ctx: click.Context, project: str | None) -> None:
    """Show backup statistics.

    \b
    Examples:
      hostkit backup stats           Show stats for all projects
      hostkit backup stats myapp     Show stats for myapp only
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()
        stats = service.get_backup_stats(project)

        if formatter.json_mode:
            formatter.success(data=stats, message="Backup statistics")
        else:
            click.echo(f"\nBackup Statistics\n")
            click.echo(f"Total backups: {stats['total_backups']}")
            click.echo(f"Total size:    {format_size(stats['total_size_bytes'])}")

            if stats['by_project']:
                click.echo(f"\nBy Project:")
                click.echo(f"{'Project':<20} {'Count':<8} {'Size':<12} {'Latest'}")
                click.echo("-" * 70)
                for proj, proj_stats in stats['by_project'].items():
                    latest = proj_stats['latest'][:19].replace("T", " ") if proj_stats['latest'] else "N/A"
                    click.echo(f"{proj:<20} {proj_stats['count']:<8} {format_size(proj_stats['size_bytes']):<12} {latest}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("credentials")
@click.argument("project")
@click.pass_context
@project_owner("project")
def backup_credentials(ctx: click.Context, project: str) -> None:
    """Create a credential backup (env file only).

    This is automatically called before credential changes,
    but can be triggered manually.

    \b
    Examples:
      hostkit backup credentials myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()
        result = service.backup_credentials(project)

        if formatter.json_mode:
            formatter.success(data=result, message="Credentials backed up")
        else:
            click.echo(click.style("✓ Credentials backed up", fg="green"))
            click.echo(f"  Path: {result['backup_path']}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup.command("setup-timer")
@click.option("--time", "backup_time", default="02:00", help="Time for daily backup (HH:MM, default: 02:00)")
@click.option("--r2", "enable_r2", is_flag=True, help="Enable R2 cloud backup sync")
@click.pass_context
def setup_timer(ctx: click.Context, backup_time: str, enable_r2: bool) -> None:
    """Set up automated daily backup timer.

    Creates and enables systemd timer for daily backups at the specified time.
    Default is 02:00 (2 AM).

    Use --r2 to also upload backups to R2 cloud storage with extended retention
    (30 daily + 12 weekly).

    \b
    Examples:
      hostkit backup setup-timer              Set up backup at 2 AM
      hostkit backup setup-timer --r2         Set up backup with R2 sync
      hostkit backup setup-timer --time 03:30 Set up backup at 3:30 AM
    """
    import shutil
    import subprocess
    from pathlib import Path

    formatter = get_formatter(ctx)

    # Validate time format
    try:
        hour, minute = backup_time.split(":")
        int(hour)
        int(minute)
    except ValueError:
        formatter.error(
            code="INVALID_TIME",
            message=f"Invalid time format: {backup_time}",
            suggestion="Use HH:MM format (e.g., 02:00)",
        )
        raise SystemExit(1)

    try:
        # Find template files
        template_dir = Path(__file__).parent.parent.parent.parent / "templates"
        service_template = template_dir / "hostkit-backup.service"
        timer_template = template_dir / "hostkit-backup.timer"

        # Fallback paths for installed package
        if not service_template.exists():
            template_dir = Path("/var/lib/hostkit/templates")
            service_template = template_dir / "hostkit-backup.service"
            timer_template = template_dir / "hostkit-backup.timer"

        if not service_template.exists():
            formatter.error(
                code="TEMPLATE_NOT_FOUND",
                message="Backup service template not found",
                suggestion="Ensure HostKit is properly installed",
            )
            raise SystemExit(1)

        # Read service template and customize for R2 if needed
        service_content = service_template.read_text()
        if enable_r2:
            # Add --r2 flag to the ExecStart command
            service_content = service_content.replace(
                "hostkit backup run-all --json",
                "hostkit backup run-all --json --r2"
            )

        # Read and customize timer template
        timer_content = timer_template.read_text()
        timer_content = timer_content.replace(
            "OnCalendar=*-*-* 02:00:00",
            f"OnCalendar=*-*-* {backup_time}:00"
        )

        # Install service and timer
        systemd_dir = Path("/etc/systemd/system")
        service_dest = systemd_dir / "hostkit-backup.service"
        timer_dest = systemd_dir / "hostkit-backup.timer"

        # Write customized service file
        service_dest.write_text(service_content)

        # Write customized timer
        timer_dest.write_text(timer_content)

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

        # Enable and start timer
        subprocess.run(["systemctl", "enable", "hostkit-backup.timer"], check=True, capture_output=True)
        subprocess.run(["systemctl", "start", "hostkit-backup.timer"], check=True, capture_output=True)

        # Get timer status
        result = subprocess.run(
            ["systemctl", "status", "hostkit-backup.timer"],
            capture_output=True,
            text=True,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "service_file": str(service_dest),
                    "timer_file": str(timer_dest),
                    "backup_time": backup_time,
                    "r2_enabled": enable_r2,
                    "enabled": True,
                },
                message="Backup timer configured",
            )
        else:
            click.echo(click.style("\n✓ Backup timer configured\n", fg="green", bold=True))
            click.echo(f"  Service: {service_dest}")
            click.echo(f"  Timer:   {timer_dest}")
            click.echo(f"  Time:    Daily at {backup_time}")
            if enable_r2:
                click.echo(click.style("  R2:      Enabled (30 daily + 12 weekly retention)", fg="blue"))
            click.echo(f"\nTimer is now active. View status with:")
            click.echo("  systemctl status hostkit-backup.timer")
            click.echo("  systemctl list-timers hostkit-backup.timer")

    except subprocess.CalledProcessError as e:
        formatter.error(
            code="SYSTEMD_ERROR",
            message=f"Failed to configure systemd: {e}",
            suggestion="Check systemd is running and you have root permissions",
        )
        raise SystemExit(1)
    except PermissionError:
        formatter.error(
            code="PERMISSION_DENIED",
            message="Permission denied when writing systemd files",
            suggestion="Run this command as root or with sudo",
        )
        raise SystemExit(1)


@backup.command("run-all")
@click.option("--type", "backup_type", default="full", type=click.Choice(["full", "db", "files"]), help="Backup type (default: full)")
@click.option("--rotate/--no-rotate", default=True, help="Run rotation after backup (default: yes)")
@click.option("--r2", "upload_r2", is_flag=True, help="Also upload backups to R2 cloud storage")
@click.pass_context
def run_all_backups(ctx: click.Context, backup_type: str, rotate: bool, upload_r2: bool) -> None:
    """Create backups for all projects (for scheduled tasks).

    This command is intended for use with systemd timers or cron jobs.
    It creates backups for all projects and optionally applies retention policy.

    \b
    Examples:
      hostkit backup run-all                  Full backup + rotation
      hostkit backup run-all --r2             Full backup + R2 sync + rotation
      hostkit backup run-all --type db        Database backup only
      hostkit backup run-all --no-rotate      Skip rotation
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if not formatter.json_mode:
            msg = f"Creating {backup_type} backups for all projects..."
            if upload_r2:
                msg = msg[:-3] + " (with R2 sync)..."
            click.echo(msg)

        backups = service.create_all_backups(backup_type, upload_to_r2=upload_r2)

        rotation_results = None
        r2_rotation_results = None

        if rotate:
            if not formatter.json_mode:
                click.echo("Applying local retention policy...")
            rotation_results = service.rotate_all_backups()

            # Also rotate R2 backups if R2 is enabled
            if upload_r2:
                if not formatter.json_mode:
                    click.echo("Applying R2 retention policy...")
                try:
                    r2_rotation_results = service.rotate_r2_backups(None)
                except Exception:
                    # R2 rotation is best-effort, don't fail the whole backup
                    pass

        if formatter.json_mode:
            formatter.success(
                data={
                    "backups_created": len(backups),
                    "backups": [
                        {
                            "id": b.id,
                            "project": b.project,
                            "type": b.backup_type,
                            "size_bytes": b.size_bytes,
                            "r2_synced": b.r2_synced,
                            "r2_key": b.r2_key,
                        }
                        for b in backups
                    ],
                    "rotation": rotation_results,
                    "r2_rotation": r2_rotation_results,
                },
                message=f"Created {len(backups)} backup(s)",
            )
        else:
            click.echo(click.style(f"\n✓ Created {len(backups)} backup(s)\n", fg="green", bold=True))
            for b in backups:
                r2_marker = click.style(" [R2]", fg="blue") if b.r2_synced else ""
                click.echo(f"  {b.project}: {b.id} ({format_size(b.size_bytes)}){r2_marker}")

            if rotation_results:
                total_deleted = sum(r["deleted_count"] for r in rotation_results.values())
                click.echo(f"\nLocal rotation: deleted {total_deleted} old backup(s)")

            if r2_rotation_results:
                click.echo(f"R2 rotation: deleted {r2_rotation_results['total_deleted']} old backup(s)")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# R2 Cloud Backup Commands
# =============================================================================


@backup.group("r2")
def backup_r2() -> None:
    """R2 cloud backup management commands.

    Manage backups stored in Cloudflare R2 with extended retention (30 daily + 12 weekly).

    \b
    The central hostkit-backups bucket stores all project backups.
    R2 provides zero egress fees for downloads.
    """
    pass


@backup_r2.command("sync")
@click.argument("backup_id")
@click.pass_context
def r2_sync(ctx: click.Context, backup_id: str) -> None:
    """Sync a local backup to R2 cloud storage.

    \b
    Examples:
      hostkit backup r2 sync myapp_full_20250101_120000
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()

        if not formatter.json_mode:
            click.echo(f"Uploading backup to R2: {backup_id}...")

        result = service.upload_to_r2(backup_id)

        if formatter.json_mode:
            formatter.success(data=result, message="Backup synced to R2")
        else:
            click.echo(click.style("\n✓ Backup synced to R2\n", fg="green", bold=True))
            click.echo(f"  Backup ID:   {result['backup_id']}")
            click.echo(f"  R2 Key:      {result['r2_key']}")
            click.echo(f"  Size:        {format_size(result['size_bytes'])}")
            click.echo(f"  Upload Time: {result['upload_time_seconds']}s")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup_r2.command("list")
@click.argument("project", required=False)
@click.pass_context
def r2_list(ctx: click.Context, project: str | None) -> None:
    """List backups stored in R2.

    Shows all R2 backups, including those where local file was deleted.

    \b
    Examples:
      hostkit backup r2 list           List all R2 backups
      hostkit backup r2 list myapp     List R2 backups for myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()
        backups = service.list_r2_backups(project)

        if formatter.json_mode:
            formatter.success(
                data={"backups": backups},
                message=f"Found {len(backups)} R2 backup(s)",
            )
        else:
            if not backups:
                click.echo("No R2 backups found.")
                return

            click.echo(f"\nR2 Backups ({len(backups)} total):\n")
            click.echo(f"{'Key':<60} {'Size':<10} {'Modified'}")
            click.echo("-" * 90)

            current_project = None
            for b in backups:
                if b["project"] != current_project:
                    if current_project is not None:
                        click.echo("")
                    click.echo(click.style(f"Project: {b['project']}", fg="cyan", bold=True))
                    current_project = b["project"]

                size_str = format_size(b["size_bytes"])
                modified = b["last_modified"][:19].replace("T", " ")
                key_short = b["key"].split("/", 1)[-1] if "/" in b["key"] else b["key"]
                click.echo(f"  {key_short:<58} {size_str:<10} {modified}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup_r2.command("rotate")
@click.argument("project", required=False)
@click.option("--all", "rotate_all", is_flag=True, help="Rotate all projects")
@click.pass_context
def r2_rotate(ctx: click.Context, project: str | None, rotate_all: bool) -> None:
    """Apply R2 retention policy (30 daily + 12 weekly).

    \b
    Examples:
      hostkit backup r2 rotate myapp
      hostkit backup r2 rotate --all
    """
    formatter = get_formatter(ctx)

    if not project and not rotate_all:
        formatter.error(
            code="MISSING_ARGUMENT",
            message="Specify a project or use --all",
            suggestion="hostkit backup r2 rotate myapp OR hostkit backup r2 rotate --all",
        )
        raise SystemExit(1)

    try:
        service = BackupService()

        if not formatter.json_mode:
            target = "all projects" if rotate_all else project
            click.echo(f"Applying R2 retention policy to {target}...")

        result = service.rotate_r2_backups(None if rotate_all else project)

        if formatter.json_mode:
            formatter.success(data=result, message="R2 rotation complete")
        else:
            click.echo(click.style("\n✓ R2 rotation complete\n", fg="green", bold=True))
            click.echo(f"  Total deleted: {result['total_deleted']}")
            for proj, stats in result["projects"].items():
                click.echo(f"  {proj}: kept {stats['kept_daily']} daily + {stats['kept_weekly']} weekly, deleted {stats['deleted_count']}")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup_r2.command("download")
@click.argument("backup_id")
@click.argument("destination", required=False)
@click.pass_context
def r2_download(ctx: click.Context, backup_id: str, destination: str | None) -> None:
    """Download a backup from R2.

    \b
    Examples:
      hostkit backup r2 download myapp_full_20250101_120000
      hostkit backup r2 download myapp_full_20250101_120000 /tmp/backup.tar.gz
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()
        from pathlib import Path

        dest_path = Path(destination) if destination else None

        if not formatter.json_mode:
            click.echo(f"Downloading backup from R2: {backup_id}...")

        result = service.download_from_r2(backup_id=backup_id, dest_path=dest_path)

        if formatter.json_mode:
            formatter.success(data=result, message="Backup downloaded from R2")
        else:
            click.echo(click.style("\n✓ Backup downloaded from R2\n", fg="green", bold=True))
            click.echo(f"  R2 Key:        {result['r2_key']}")
            click.echo(f"  Local Path:    {result['local_path']}")
            click.echo(f"  Size:          {format_size(result['size_bytes'])}")
            click.echo(f"  Download Time: {result['download_time_seconds']}s")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@backup_r2.command("status")
@click.pass_context
def r2_status(ctx: click.Context) -> None:
    """Show R2 backup storage status.

    \b
    Examples:
      hostkit backup r2 status
    """
    formatter = get_formatter(ctx)

    try:
        service = BackupService()
        status = service.get_r2_status()

        if formatter.json_mode:
            formatter.success(data=status, message="R2 backup status")
        else:
            click.echo("\nR2 Backup Storage Status:\n")

            if not status.get("configured"):
                click.echo(click.style("  R2 not configured", fg="red"))
                if status.get("error"):
                    click.echo(f"  Error: {status['error']}")
                return

            click.echo(f"  Endpoint: {status['endpoint']}")
            click.echo(f"  Bucket:   {status['bucket']}")

            if not status.get("bucket_exists"):
                click.echo(click.style("  Bucket does not exist yet (will be created on first backup)", fg="yellow"))
                return

            click.echo(f"  Objects:  {status['total_objects']}")
            click.echo(f"  Size:     {format_size(status['total_size_bytes'])}")

            if status.get("by_project"):
                click.echo("\n  By Project:")
                for proj, count in status["by_project"].items():
                    click.echo(f"    {proj}: {count} backup(s)")

    except BackupServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

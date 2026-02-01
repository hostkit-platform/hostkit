"""Database migration commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.services.alert_service import send_alert
from hostkit.services.migrate_service import MigrateService, MigrateServiceError


@click.command()
@click.argument("project")
@click.option("--django", "framework", flag_value="django", help="Run Django migrations")
@click.option("--alembic", "framework", flag_value="alembic", help="Run Alembic migrations")
@click.option("--prisma", "framework", flag_value="prisma", help="Run Prisma migrations")
@click.option("--cmd", "custom_cmd", help="Custom migration command")
@click.option("--dry-run", is_flag=True, help="Show command without executing")
@click.option(
    "--checkpoint/--no-checkpoint",
    default=True,
    help="Create checkpoint before migrating (default: yes)",
)
@click.pass_context
@project_owner("project")
def migrate(
    ctx: click.Context,
    project: str,
    framework: str | None,
    custom_cmd: str | None,
    dry_run: bool,
    checkpoint: bool,
):
    """Run database migrations for a project.

    Auto-detects the migration framework (Django, Alembic, Prisma) unless
    explicitly specified.

    By default, a database checkpoint is created before running migrations.
    If migrations fail, you can restore using: hostkit checkpoint restore <project> <id>

    Examples:
        hostkit migrate myapp                    # Auto-detect and run (with checkpoint)
        hostkit migrate myapp --django           # Force Django migrations
        hostkit migrate myapp --alembic          # Force Alembic migrations
        hostkit migrate myapp --prisma           # Force Prisma migrations
        hostkit migrate myapp --cmd "custom cmd" # Run custom command
        hostkit migrate myapp --dry-run          # Show what would run
        hostkit migrate myapp --no-checkpoint    # Skip checkpoint creation
    """
    service = MigrateService()
    checkpoint_id = None

    # First, detect framework if not specified (for user feedback)
    if not framework and not custom_cmd:
        try:
            detected = service.detect_framework(project)
            if detected:
                click.echo(f"Detected migration framework: {detected}")
            else:
                click.echo(click.style("Warning: No migration framework detected", fg="yellow"))
        except MigrateServiceError:
            pass  # Will be handled by migrate() call

    # Create checkpoint before migration (unless dry-run or --no-checkpoint)
    if checkpoint and not dry_run:
        try:
            from hostkit.services.checkpoint_service import (
                CheckpointService,
                CheckpointServiceError,
            )

            checkpoint_service = CheckpointService()

            # Check if project has a database
            from hostkit.services.database_service import DatabaseService

            db_service = DatabaseService()

            if db_service.database_exists(project):
                click.echo("Creating pre-migration checkpoint...")
                cp = checkpoint_service.create_checkpoint(
                    project_name=project,
                    label="pre-migration",
                    checkpoint_type="pre_migration",
                    trigger_source="migrate",
                )
                checkpoint_id = cp.id
                click.echo(click.style(f"  Checkpoint {cp.id} created", fg="green"))
            else:
                click.echo(click.style("  No database found, skipping checkpoint", fg="yellow"))

        except CheckpointServiceError as e:
            click.echo(
                click.style(f"  Warning: Could not create checkpoint: {e.message}", fg="yellow")
            )
            click.echo("  Continuing without checkpoint...")

    try:
        result = service.migrate(
            project=project,
            framework=framework,
            custom_cmd=custom_cmd,
            dry_run=dry_run,
        )
    except MigrateServiceError as e:
        # Send failure alert
        try:
            send_alert(
                project_name=project,
                event_type="migrate",
                event_status="failure",
                data={"error": e.message, "code": e.code, "checkpoint_id": checkpoint_id},
            )
        except Exception:
            pass  # Alerts are non-blocking

        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)

        # If we created a checkpoint, suggest rollback
        if checkpoint_id:
            click.echo("\nTo rollback to pre-migration state:")
            click.echo(f"  hostkit checkpoint restore {project} {checkpoint_id}")

        raise click.ClickException(e.message)

    if dry_run:
        click.echo(click.style("\n[DRY RUN]", fg="yellow"))
        click.echo(f"Framework: {result.framework}")
        click.echo(f"Command: {result.command}")
        click.echo(f"Directory: /home/{project}/app")
        if checkpoint:
            click.echo("Checkpoint: Would be created before migration")
        return

    # Show result
    click.echo(f"\nFramework: {result.framework}")
    click.echo(f"Command: {result.command}")
    click.echo("-" * 40)

    if result.output:
        click.echo(result.output)
    else:
        click.echo("(no output)")

    click.echo("-" * 40)

    if result.success:
        click.echo(click.style("Migration completed successfully", fg="green"))
        if checkpoint_id:
            click.echo(f"  Pre-migration checkpoint: {checkpoint_id}")

        # Send success alert
        try:
            send_alert(
                project_name=project,
                event_type="migrate",
                event_status="success",
                data={
                    "framework": result.framework,
                    "checkpoint_id": checkpoint_id,
                },
            )
        except Exception:
            pass  # Alerts are non-blocking
    else:
        click.echo(click.style("Migration failed", fg="red"))

        # Send failure alert
        try:
            send_alert(
                project_name=project,
                event_type="migrate",
                event_status="failure",
                data={
                    "framework": result.framework,
                    "checkpoint_id": checkpoint_id,
                    "output": result.output[:500] if result.output else None,
                },
            )
        except Exception:
            pass  # Alerts are non-blocking

        if checkpoint_id:
            click.echo("\nTo rollback to pre-migration state:")
            click.echo(f"  hostkit checkpoint restore {project} {checkpoint_id}")
        raise click.ClickException("Migration command returned non-zero exit code")

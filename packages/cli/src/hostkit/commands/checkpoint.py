"""Database checkpoint CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.checkpoint_service import CheckpointService, CheckpointServiceError


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
def checkpoint() -> None:
    """Database checkpoint management.

    Create point-in-time database snapshots for safe rollbacks during
    migrations, deployments, and other risky operations.

    \b
    Checkpoint Types:
      manual        - Created by user/AI via CLI (never auto-deleted)
      pre_migration - Auto-created before migrations (30 day retention)
      pre_restore   - Auto-created before restores (7 day retention)
      auto          - System-generated (7 day retention)

    \b
    Usage:
      hostkit checkpoint create myapp --label "before refactor"
      hostkit checkpoint list myapp
      hostkit checkpoint restore myapp 5
      hostkit checkpoint delete myapp 5 --force
    """
    pass


@checkpoint.command("create")
@click.argument("project")
@click.option("--label", "-l", default=None, help="Human-readable label for the checkpoint")
@click.pass_context
@project_owner("project")
def create_checkpoint(ctx: click.Context, project: str, label: str | None) -> None:
    """Create a database checkpoint for a project.

    Creates a compressed pg_dump snapshot stored in /backups/{project}/checkpoints/.
    Manual checkpoints are never auto-deleted.

    \b
    Examples:
      hostkit checkpoint create myapp
      hostkit checkpoint create myapp --label "before migration"
      hostkit checkpoint create myapp -l "pre-refactor"
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()

        if not formatter.json_mode:
            click.echo(f"Creating checkpoint for {project}...")

        checkpoint = service.create_checkpoint(
            project_name=project,
            label=label,
            checkpoint_type="manual",
            trigger_source="user",
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "checkpoint_id": checkpoint.id,
                    "project": checkpoint.project_name,
                    "label": checkpoint.label,
                    "type": checkpoint.checkpoint_type,
                    "database": checkpoint.database_name,
                    "path": checkpoint.backup_path,
                    "size_bytes": checkpoint.size_bytes,
                    "created_at": checkpoint.created_at,
                    "created_by": checkpoint.created_by,
                    "expires_at": checkpoint.expires_at,
                },
                message="Checkpoint created successfully",
            )
        else:
            click.echo(click.style("\nCheckpoint created successfully\n", fg="green", bold=True))
            click.echo(f"  ID:       {checkpoint.id}")
            if checkpoint.label:
                click.echo(f"  Label:    {checkpoint.label}")
            click.echo(f"  Database: {checkpoint.database_name}")
            click.echo(f"  Size:     {format_size(checkpoint.size_bytes)}")
            click.echo(f"  Path:     {checkpoint.backup_path}")
            click.echo(f"  Created:  {checkpoint.created_at}")
            if checkpoint.expires_at:
                click.echo(f"  Expires:  {checkpoint.expires_at}")
            else:
                click.echo("  Expires:  Never (manual checkpoint)")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("list")
@click.argument("project")
@click.option("--limit", "-n", default=20, help="Maximum number of checkpoints to show (default: 20)")
@click.option("--type", "checkpoint_type", default=None,
              type=click.Choice(["manual", "pre_migration", "pre_restore", "auto"]),
              help="Filter by checkpoint type")
@click.pass_context
def list_checkpoints(ctx: click.Context, project: str, limit: int, checkpoint_type: str | None) -> None:
    """List database checkpoints for a project.

    Checkpoints are shown most recent first.

    \b
    Examples:
      hostkit checkpoint list myapp
      hostkit checkpoint list myapp --limit 5
      hostkit checkpoint list myapp --type manual
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()
        checkpoints = service.list_checkpoints(
            project_name=project,
            checkpoint_type=checkpoint_type,
            limit=limit,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "checkpoints": [
                        {
                            "id": cp.id,
                            "label": cp.label,
                            "type": cp.checkpoint_type,
                            "trigger_source": cp.trigger_source,
                            "database": cp.database_name,
                            "path": cp.backup_path,
                            "size_bytes": cp.size_bytes,
                            "created_at": cp.created_at,
                            "created_by": cp.created_by,
                            "expires_at": cp.expires_at,
                        }
                        for cp in checkpoints
                    ],
                    "count": len(checkpoints),
                    "project": project,
                },
                message=f"Found {len(checkpoints)} checkpoint(s)",
            )
        else:
            if not checkpoints:
                click.echo(f"No checkpoints found for {project}.")
                click.echo("\nCreate one with: hostkit checkpoint create " + project)
                return

            click.echo(f"\nCheckpoints for {project} ({len(checkpoints)} total):\n")

            # Header
            click.echo(f"{'ID':<6} {'Type':<14} {'Label':<25} {'Size':<10} {'Created':<20} {'Expires'}")
            click.echo("-" * 100)

            for cp in checkpoints:
                label = cp.label[:22] + "..." if cp.label and len(cp.label) > 25 else (cp.label or "-")
                size = format_size(cp.size_bytes)
                created = cp.created_at[:19].replace("T", " ")
                expires = cp.expires_at[:10] if cp.expires_at else "Never"

                click.echo(f"{cp.id:<6} {cp.checkpoint_type:<14} {label:<25} {size:<10} {created:<20} {expires}")

            click.echo("")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("info")
@click.argument("project")
@click.argument("checkpoint_id", type=int)
@click.pass_context
def checkpoint_info(ctx: click.Context, project: str, checkpoint_id: int) -> None:
    """Show detailed information about a checkpoint.

    \b
    Examples:
      hostkit checkpoint info myapp 5
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()
        cp = service.get_checkpoint(checkpoint_id)

        # Verify project matches
        if cp.project_name != project:
            formatter.error(
                code="CHECKPOINT_MISMATCH",
                message=f"Checkpoint {checkpoint_id} belongs to project '{cp.project_name}', not '{project}'",
                suggestion="Specify the correct project or checkpoint ID",
            )
            raise SystemExit(1)

        if formatter.json_mode:
            formatter.success(
                data={
                    "id": cp.id,
                    "project": cp.project_name,
                    "label": cp.label,
                    "type": cp.checkpoint_type,
                    "trigger_source": cp.trigger_source,
                    "database": cp.database_name,
                    "path": cp.backup_path,
                    "size_bytes": cp.size_bytes,
                    "created_at": cp.created_at,
                    "created_by": cp.created_by,
                    "expires_at": cp.expires_at,
                },
                message=f"Checkpoint {checkpoint_id} details",
            )
        else:
            click.echo(f"\nCheckpoint {checkpoint_id}\n")
            click.echo(f"  Project:        {cp.project_name}")
            click.echo(f"  Label:          {cp.label or '-'}")
            click.echo(f"  Type:           {cp.checkpoint_type}")
            click.echo(f"  Trigger:        {cp.trigger_source or '-'}")
            click.echo(f"  Database:       {cp.database_name}")
            click.echo(f"  Path:           {cp.backup_path}")
            click.echo(f"  Size:           {format_size(cp.size_bytes)}")
            click.echo(f"  Created at:     {cp.created_at}")
            click.echo(f"  Created by:     {cp.created_by}")
            if cp.expires_at:
                click.echo(f"  Expires at:     {cp.expires_at}")
            else:
                click.echo("  Expires at:     Never (manual checkpoint)")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("restore")
@click.argument("project")
@click.argument("checkpoint_id", type=int)
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.option("--no-safety", is_flag=True, help="Skip creating a safety checkpoint before restore")
@click.pass_context
@project_owner("project")
def restore_checkpoint(ctx: click.Context, project: str, checkpoint_id: int, force: bool, no_safety: bool) -> None:
    """Restore a database from a checkpoint.

    WARNING: This will drop the current database and restore from the checkpoint.
    A safety checkpoint is automatically created before restoring (unless --no-safety).

    \b
    Examples:
      hostkit checkpoint restore myapp 5
      hostkit checkpoint restore myapp 5 --force
      hostkit checkpoint restore myapp 5 --no-safety --force
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()

        # Get checkpoint info
        cp = service.get_checkpoint(checkpoint_id)

        # Verify project matches
        if cp.project_name != project:
            formatter.error(
                code="CHECKPOINT_MISMATCH",
                message=f"Checkpoint {checkpoint_id} belongs to project '{cp.project_name}', not '{project}'",
                suggestion="Specify the correct project or checkpoint ID",
            )
            raise SystemExit(1)

        # Confirm if not forced
        if not force and not formatter.json_mode:
            click.echo(f"\nAbout to restore checkpoint {checkpoint_id}")
            click.echo(f"  Project:    {project}")
            click.echo(f"  Database:   {cp.database_name}")
            click.echo(f"  Label:      {cp.label or '-'}")
            click.echo(f"  Created:    {cp.created_at}")
            click.echo(f"  Size:       {format_size(cp.size_bytes)}")
            click.echo("")
            click.echo(click.style("WARNING: This will drop the current database and restore from checkpoint!", fg="yellow", bold=True))

            if not no_safety:
                click.echo("\nA safety checkpoint will be created before restoring.")

            if not click.confirm("\nDo you want to proceed?"):
                click.echo("Restore cancelled.")
                return

        if not formatter.json_mode:
            click.echo(f"\nRestoring checkpoint {checkpoint_id} for {project}...")

        result = service.restore_checkpoint(
            project_name=project,
            checkpoint_id=checkpoint_id,
            create_pre_restore=not no_safety,
        )

        if formatter.json_mode:
            formatter.success(data=result, message="Checkpoint restored successfully")
        else:
            click.echo(click.style("\nCheckpoint restored successfully\n", fg="green", bold=True))
            click.echo(f"  Database:     {result['database']}")
            click.echo(f"  Restored from: Checkpoint {result['restored_from_checkpoint']}")
            if result.get('checkpoint_label'):
                click.echo(f"  Label:        {result['checkpoint_label']}")
            if result.get('pre_restore_checkpoint_id'):
                click.echo(f"  Safety checkpoint: {result['pre_restore_checkpoint_id']} (in case of issues)")
            click.echo(f"  Restored at:  {result['restored_at']}")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("delete")
@click.argument("project")
@click.argument("checkpoint_id", type=int)
@click.option("--force", is_flag=True, required=True, help="Confirm deletion (required)")
@click.pass_context
@project_owner("project")
def delete_checkpoint(ctx: click.Context, project: str, checkpoint_id: int, force: bool) -> None:
    """Delete a checkpoint.

    Deletes both the checkpoint record and the backup file.

    \b
    Examples:
      hostkit checkpoint delete myapp 5 --force
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()

        result = service.delete_checkpoint(
            project_name=project,
            checkpoint_id=checkpoint_id,
            force=force,
        )

        if formatter.json_mode:
            formatter.success(data=result, message="Checkpoint deleted")
        else:
            click.echo(click.style(f"Checkpoint {checkpoint_id} deleted", fg="green"))
            click.echo(f"  Freed: {format_size(result['size_bytes'])}")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("cleanup")
@click.pass_context
def cleanup_checkpoints(ctx: click.Context) -> None:
    """Remove expired checkpoints.

    Deletes checkpoints that have passed their expiry date based on retention policy:
      - manual: Never expires
      - pre_migration: 30 days
      - pre_restore: 7 days
      - auto: 7 days

    This command is safe to run regularly (e.g., via cron).

    \b
    Examples:
      hostkit checkpoint cleanup
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()

        if not formatter.json_mode:
            click.echo("Cleaning up expired checkpoints...")

        result = service.cleanup_expired_checkpoints()

        if formatter.json_mode:
            formatter.success(data=result, message="Cleanup complete")
        else:
            if result["deleted_count"] > 0:
                click.echo(click.style(f"\nDeleted {result['deleted_count']} expired checkpoint(s)", fg="green"))
                click.echo(f"  Freed: {format_size(result['freed_bytes'])}")
            else:
                click.echo("\nNo expired checkpoints found.")

            if result["errors"]:
                click.echo(click.style(f"\nErrors ({len(result['errors'])}):", fg="yellow"))
                for err in result["errors"]:
                    click.echo(f"  - Checkpoint {err['checkpoint_id']}: {err['error']}")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@checkpoint.command("latest")
@click.argument("project")
@click.option("--type", "checkpoint_type", default=None,
              type=click.Choice(["manual", "pre_migration", "pre_restore", "auto"]),
              help="Filter by checkpoint type")
@click.pass_context
def latest_checkpoint(ctx: click.Context, project: str, checkpoint_type: str | None) -> None:
    """Show the most recent checkpoint for a project.

    \b
    Examples:
      hostkit checkpoint latest myapp
      hostkit checkpoint latest myapp --type pre_migration
    """
    formatter = get_formatter(ctx)

    try:
        service = CheckpointService()
        cp = service.get_latest_checkpoint(
            project_name=project,
            checkpoint_type=checkpoint_type,
        )

        if cp is None:
            if formatter.json_mode:
                formatter.success(
                    data={"checkpoint": None},
                    message="No checkpoints found",
                )
            else:
                type_filter = f" of type '{checkpoint_type}'" if checkpoint_type else ""
                click.echo(f"No checkpoints{type_filter} found for {project}.")
            return

        if formatter.json_mode:
            formatter.success(
                data={
                    "id": cp.id,
                    "project": cp.project_name,
                    "label": cp.label,
                    "type": cp.checkpoint_type,
                    "trigger_source": cp.trigger_source,
                    "database": cp.database_name,
                    "path": cp.backup_path,
                    "size_bytes": cp.size_bytes,
                    "created_at": cp.created_at,
                    "created_by": cp.created_by,
                    "expires_at": cp.expires_at,
                },
                message=f"Latest checkpoint: {cp.id}",
            )
        else:
            click.echo(f"\nLatest checkpoint for {project}:\n")
            click.echo(f"  ID:         {cp.id}")
            click.echo(f"  Label:      {cp.label or '-'}")
            click.echo(f"  Type:       {cp.checkpoint_type}")
            click.echo(f"  Database:   {cp.database_name}")
            click.echo(f"  Size:       {format_size(cp.size_bytes)}")
            click.echo(f"  Created:    {cp.created_at}")
            click.echo(f"  Created by: {cp.created_by}")
            if cp.expires_at:
                click.echo(f"  Expires:    {cp.expires_at}")
            else:
                click.echo("  Expires:    Never")

    except CheckpointServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

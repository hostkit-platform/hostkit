"""Sandbox commands for HostKit - temporary isolated project clones."""

import click

from hostkit.access import root_only
from hostkit.output import OutputFormatter
from hostkit.services.sandbox_service import (
    SandboxService,
    SandboxServiceError,
)


@click.group("sandbox")
def sandbox() -> None:
    """Manage sandbox environments.

    Sandboxes are temporary, isolated clones of projects for safe experimentation.
    They include a copy of the code, database (optional), and run on a separate
    port with a nip.io domain.

    Key features:
    - Automatic expiration (default: 24 hours)
    - Maximum 3 sandboxes per project
    - Promote successful sandboxes to replace the source
    """
    pass


@sandbox.command("create")
@click.argument("project")
@click.option(
    "--ttl",
    default="24h",
    help="Time-to-live before auto-expiration (e.g., 24h, 48h)",
)
@click.option(
    "--no-db",
    is_flag=True,
    help="Skip database cloning",
)
@click.pass_context
@root_only
def create(ctx: click.Context, project: str, ttl: str, no_db: bool) -> None:
    """Create a sandbox from an existing project.

    Creates an isolated clone with:
    - Separate Linux user
    - Copy of current code
    - Cloned database (unless --no-db)
    - Unique port and nip.io domain
    - Systemd service

    Examples:
        hostkit sandbox create myapp
        hostkit sandbox create myapp --ttl 48h
        hostkit sandbox create myapp --no-db
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    # Parse TTL
    try:
        ttl_hours = _parse_ttl(ttl)
    except ValueError as e:
        formatter.error("INVALID_TTL", str(e), "Use format like '24h' or '48h'")
        return

    try:
        sandbox_info = service.create_sandbox(
            source_project=project,
            ttl_hours=ttl_hours,
            include_db=not no_db,
        )

        formatter.success(
            data={
                "sandbox_name": sandbox_info.sandbox_name,
                "source_project": sandbox_info.source_project,
                "port": sandbox_info.port,
                "domain": sandbox_info.domain,
                "db_name": sandbox_info.db_name,
                "expires_at": sandbox_info.expires_at,
                "status": sandbox_info.status,
            },
            message=f"Sandbox '{sandbox_info.sandbox_name}' created successfully",
        )

    except SandboxServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@sandbox.command("list")
@click.argument("project", required=False)
@click.option(
    "--all",
    "include_expired",
    is_flag=True,
    help="Include expired sandboxes",
)
@click.pass_context
def list_sandboxes(
    ctx: click.Context,
    project: str | None,
    include_expired: bool,
) -> None:
    """List sandboxes.

    Without a project argument, lists all sandboxes.
    With a project argument, lists sandboxes for that project only.

    Examples:
        hostkit sandbox list
        hostkit sandbox list myapp
        hostkit sandbox list --all
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    sandboxes = service.list_sandboxes(
        source_project=project,
        include_expired=include_expired,
    )

    if not sandboxes:
        message = "No sandboxes found"
        if project:
            message = f"No sandboxes found for project '{project}'"
        formatter.success([], message)
        return

    data = [
        {
            "sandbox_name": s.sandbox_name,
            "source_project": s.source_project,
            "status": s.status,
            "port": s.port,
            "domain": s.domain,
            "expires_at": s.expires_at[:16],  # Truncate to minute
            "has_db": s.db_name is not None,
        }
        for s in sandboxes
    ]

    columns = [
        ("sandbox_name", "Sandbox"),
        ("source_project", "Source"),
        ("status", "Status"),
        ("domain", "Domain"),
        ("expires_at", "Expires"),
    ]

    formatter.table(data, columns, title="Sandboxes", message="Sandboxes retrieved")


@sandbox.command("info")
@click.argument("sandbox_name")
@click.pass_context
def info(ctx: click.Context, sandbox_name: str) -> None:
    """Show detailed information about a sandbox.

    Example:
        hostkit sandbox info myapp-sandbox-a3f9
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    try:
        sandbox_info = service.get_sandbox(sandbox_name)

        formatter.success(
            data={
                "id": sandbox_info.id,
                "sandbox_name": sandbox_info.sandbox_name,
                "source_project": sandbox_info.source_project,
                "source_release": sandbox_info.source_release,
                "status": sandbox_info.status,
                "port": sandbox_info.port,
                "domain": sandbox_info.domain,
                "db_name": sandbox_info.db_name,
                "expires_at": sandbox_info.expires_at,
                "created_at": sandbox_info.created_at,
                "created_by": sandbox_info.created_by,
                "home": f"/home/{sandbox_info.sandbox_name}",
                "service": f"hostkit-{sandbox_info.sandbox_name}",
            },
            message=f"Sandbox '{sandbox_name}' info",
        )

    except SandboxServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@sandbox.command("delete")
@click.argument("sandbox_name")
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Confirm deletion (required)",
)
@click.pass_context
@root_only
def delete(ctx: click.Context, sandbox_name: str, force: bool) -> None:
    """Delete a sandbox and all its resources.

    This will:
    - Stop the sandbox service
    - Remove systemd service file
    - Remove Nginx configuration
    - Delete the database (if cloned)
    - Delete the Linux user and home directory
    - Remove log files

    Requires --force flag to confirm.

    Example:
        hostkit sandbox delete myapp-sandbox-a3f9 --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    try:
        service.delete_sandbox(sandbox_name, force=force)
        formatter.success(
            {"sandbox_name": sandbox_name},
            f"Sandbox '{sandbox_name}' deleted successfully",
        )

    except SandboxServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@sandbox.command("promote")
@click.argument("sandbox_name")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without executing",
)
@click.pass_context
@root_only
def promote(ctx: click.Context, sandbox_name: str, dry_run: bool) -> None:
    """Promote a sandbox to replace its source project.

    This is a significant operation that:
    1. Stops the source project
    2. Creates a backup of the source
    3. Swaps the database (if sandbox has one)
    4. Copies sandbox code to source project
    5. Restarts the source project
    6. Deletes the sandbox

    Use --dry-run to preview what would happen.

    Examples:
        hostkit sandbox promote myapp-sandbox-a3f9
        hostkit sandbox promote myapp-sandbox-a3f9 --dry-run
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    try:
        result = service.promote_sandbox(sandbox_name, dry_run=dry_run)

        if dry_run:
            formatter.success(
                data={
                    "sandbox_name": result.sandbox_name,
                    "source_project": result.source_project,
                    "database_will_swap": result.database_swapped,
                    "code_will_swap": result.code_swapped,
                    "dry_run": True,
                },
                message="Dry run - no changes made",
            )
        else:
            formatter.success(
                data={
                    "sandbox_name": result.sandbox_name,
                    "source_project": result.source_project,
                    "backup_id": result.backup_id,
                    "database_swapped": result.database_swapped,
                    "code_swapped": result.code_swapped,
                },
                message=result.message,
            )

    except SandboxServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@sandbox.command("extend")
@click.argument("sandbox_name")
@click.option(
    "--hours",
    type=int,
    default=24,
    help="Hours to extend TTL by (default: 24)",
)
@click.pass_context
@root_only
def extend(ctx: click.Context, sandbox_name: str, hours: int) -> None:
    """Extend a sandbox's time-to-live.

    By default extends by 24 hours. Use --hours to specify different duration.

    Examples:
        hostkit sandbox extend myapp-sandbox-a3f9
        hostkit sandbox extend myapp-sandbox-a3f9 --hours 48
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    try:
        sandbox_info = service.extend_ttl(sandbox_name, hours)

        formatter.success(
            data={
                "sandbox_name": sandbox_info.sandbox_name,
                "new_expires_at": sandbox_info.expires_at,
                "extended_by_hours": hours,
            },
            message=f"Extended TTL by {hours} hours",
        )

    except SandboxServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@sandbox.command("cleanup")
@click.pass_context
@root_only
def cleanup(ctx: click.Context) -> None:
    """Delete all expired sandboxes.

    Removes all sandboxes that have passed their expiration time.
    This command can be run manually or via cron for automatic cleanup.

    Example:
        hostkit sandbox cleanup
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SandboxService()

    deleted = service.cleanup_expired()

    if deleted:
        formatter.success(
            data={
                "deleted_count": len(deleted),
                "deleted_sandboxes": deleted,
            },
            message=f"Cleaned up {len(deleted)} expired sandbox(es)",
        )
    else:
        formatter.success(
            {"deleted_count": 0},
            "No expired sandboxes to clean up",
        )


def _parse_ttl(ttl: str) -> int:
    """Parse TTL string to hours.

    Supports formats: "24h", "48h", etc.
    """
    ttl = ttl.strip().lower()

    if ttl.endswith("h"):
        try:
            hours = int(ttl[:-1])
            if hours < 1:
                raise ValueError("TTL must be at least 1 hour")
            if hours > 168:  # 1 week max
                raise ValueError("TTL cannot exceed 168 hours (1 week)")
            return hours
        except ValueError as e:
            if "invalid literal" in str(e):
                raise ValueError(f"Invalid TTL format: {ttl}")
            raise

    # Try parsing as plain number (assume hours)
    try:
        hours = int(ttl)
        if hours < 1:
            raise ValueError("TTL must be at least 1 hour")
        if hours > 168:
            raise ValueError("TTL cannot exceed 168 hours (1 week)")
        return hours
    except ValueError:
        raise ValueError(f"Invalid TTL format: {ttl}")

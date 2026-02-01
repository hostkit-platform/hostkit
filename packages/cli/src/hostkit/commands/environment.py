"""Environment commands for HostKit - multi-environment support."""

import click

from hostkit.access import root_only, project_access
from hostkit.output import OutputFormatter
from hostkit.services.environment_service import (
    EnvironmentService,
    EnvironmentServiceError,
    MAX_ENVIRONMENTS_PER_PROJECT,
)


@click.group("environment")
def environment() -> None:
    """Manage project environments.

    Environments allow projects to have separate staging/production configurations
    with optional separate databases. Each environment is a fully isolated instance
    with its own Linux user, port, and service.

    Key features:
    - Separate configurations per environment
    - Optional separate databases
    - Promotion workflow between environments
    - Maximum 5 environments per project
    """
    pass


@environment.command("create")
@click.argument("project")
@click.argument("env_name")
@click.option(
    "--with-db",
    is_flag=True,
    help="Create a separate database for this environment",
)
@click.option(
    "--share-db",
    is_flag=True,
    help="Share the parent project's database",
)
@click.pass_context
@root_only
def create(
    ctx: click.Context,
    project: str,
    env_name: str,
    with_db: bool,
    share_db: bool,
) -> None:
    """Create a new environment for a project.

    Creates an isolated environment with:
    - Separate Linux user ({project}-{env_name})
    - Unique port and nip.io domain
    - Systemd service
    - Optional separate database (--with-db)
    - Optional shared database (--share-db)

    Examples:
        hostkit environment create myapp staging
        hostkit environment create myapp production --with-db
        hostkit environment create myapp qa --share-db
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        env_info = service.create_environment(
            project_name=project,
            env_name=env_name,
            with_db=with_db,
            share_db=share_db,
        )

        linux_user = env_info.linux_user
        from hostkit.config import get_config
        domain = f"{linux_user}.{get_config().vps_ip}.nip.io"

        formatter.success(
            data={
                "project": env_info.project_name,
                "env_name": env_info.env_name,
                "linux_user": env_info.linux_user,
                "port": env_info.port,
                "domain": domain,
                "db_name": env_info.db_name,
                "share_parent_db": env_info.share_parent_db,
                "status": env_info.status,
            },
            message=f"Environment '{env_name}' created for project '{project}'",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("list")
@click.argument("project", required=False)
@click.pass_context
def list_environments(ctx: click.Context, project: str | None) -> None:
    """List environments.

    Without a project argument, lists all environments.
    With a project argument, lists environments for that project only.

    Examples:
        hostkit environment list
        hostkit environment list myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    environments = service.list_environments(project_name=project)

    if not environments:
        message = "No environments found"
        if project:
            message = f"No environments found for project '{project}'"
        formatter.success([], message)
        return

    data = [
        {
            "project": e.project_name,
            "env_name": e.env_name,
            "linux_user": e.linux_user,
            "port": e.port,
            "status": e.status,
            "has_db": e.db_name is not None,
            "share_db": e.share_parent_db,
        }
        for e in environments
    ]

    columns = [
        ("project", "Project"),
        ("env_name", "Environment"),
        ("linux_user", "User"),
        ("port", "Port"),
        ("status", "Status"),
    ]

    formatter.table(data, columns, title="Environments", message="Environments retrieved")


@environment.command("info")
@click.argument("project")
@click.argument("env_name")
@click.pass_context
@project_access("project")
def info(ctx: click.Context, project: str, env_name: str) -> None:
    """Show detailed information about an environment.

    Example:
        hostkit environment info myapp staging
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        details = service.get_environment_details(project, env_name)

        formatter.success(
            data=details,
            message=f"Environment '{env_name}' info for project '{project}'",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("delete")
@click.argument("project")
@click.argument("env_name")
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Confirm deletion (required)",
)
@click.pass_context
@root_only
def delete(ctx: click.Context, project: str, env_name: str, force: bool) -> None:
    """Delete an environment and all its resources.

    This will:
    - Stop the environment service
    - Remove systemd service file
    - Remove Nginx configuration
    - Delete the database (if separate)
    - Delete the Linux user and home directory
    - Remove log files

    Requires --force flag to confirm.

    Example:
        hostkit environment delete myapp staging --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        service.delete_environment(project, env_name, force=force)
        formatter.success(
            {"project": project, "env_name": env_name},
            f"Environment '{env_name}' deleted from project '{project}'",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("promote")
@click.argument("project")
@click.argument("source_env")
@click.argument("target_env")
@click.option(
    "--with-db",
    is_flag=True,
    help="Also copy database content",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without executing",
)
@click.pass_context
@root_only
def promote(
    ctx: click.Context,
    project: str,
    source_env: str,
    target_env: str,
    with_db: bool,
    dry_run: bool,
) -> None:
    """Promote code (and optionally data) between environments.

    This operation:
    1. Stops the target environment
    2. Copies code from source to target
    3. Optionally copies database content (--with-db)
    4. Restarts the target environment

    Note: Environment variables are NOT copied (they differ by design).

    Use --dry-run to preview what would happen.

    Examples:
        hostkit environment promote myapp staging production
        hostkit environment promote myapp staging production --with-db
        hostkit environment promote myapp staging production --dry-run
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        result = service.promote(
            project_name=project,
            source_env=source_env,
            target_env=target_env,
            with_db=with_db,
            dry_run=dry_run,
        )

        if dry_run:
            formatter.success(
                data={
                    "project": project,
                    "source_env": result.source_env,
                    "target_env": result.target_env,
                    "code_will_copy": result.code_copied,
                    "db_will_copy": result.db_copied,
                    "dry_run": True,
                },
                message="Dry run - no changes made",
            )
        else:
            formatter.success(
                data={
                    "project": project,
                    "source_env": result.source_env,
                    "target_env": result.target_env,
                    "code_copied": result.code_copied,
                    "db_copied": result.db_copied,
                },
                message=result.message,
            )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("start")
@click.argument("project")
@click.argument("env_name")
@click.pass_context
@project_access("project")
def start(ctx: click.Context, project: str, env_name: str) -> None:
    """Start an environment's service.

    Example:
        hostkit environment start myapp staging
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        service.start_environment(project, env_name)
        env = service.get_environment(project, env_name)

        formatter.success(
            {"project": project, "env_name": env_name, "service": f"hostkit-{env.linux_user}"},
            f"Environment '{env_name}' started",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("stop")
@click.argument("project")
@click.argument("env_name")
@click.pass_context
@project_access("project")
def stop(ctx: click.Context, project: str, env_name: str) -> None:
    """Stop an environment's service.

    Example:
        hostkit environment stop myapp staging
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        service.stop_environment(project, env_name)
        env = service.get_environment(project, env_name)

        formatter.success(
            {"project": project, "env_name": env_name, "service": f"hostkit-{env.linux_user}"},
            f"Environment '{env_name}' stopped",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)


@environment.command("restart")
@click.argument("project")
@click.argument("env_name")
@click.pass_context
@project_access("project")
def restart(ctx: click.Context, project: str, env_name: str) -> None:
    """Restart an environment's service.

    Example:
        hostkit environment restart myapp staging
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EnvironmentService()

    try:
        service.restart_environment(project, env_name)
        env = service.get_environment(project, env_name)

        formatter.success(
            {"project": project, "env_name": env_name, "service": f"hostkit-{env.linux_user}"},
            f"Environment '{env_name}' restarted",
        )

    except EnvironmentServiceError as e:
        formatter.error(e.code, e.message, e.suggestion)

"""Environment variable management commands for HostKit."""

import click

from hostkit.access import project_access, project_owner
from hostkit.services.env_service import EnvService, EnvServiceError


@click.group()
def env():
    """Manage environment variables for projects."""
    pass


@env.command("list")
@click.argument("project")
@click.option("--show-secrets", is_flag=True, help="Show actual secret values")
@click.pass_context
@project_access("project")
def list_env(ctx: click.Context, project: str, show_secrets: bool):
    """List environment variables for a project.

    Sensitive values (passwords, keys, tokens) are redacted by default.
    Use --show-secrets to reveal actual values.

    Examples:
        hostkit env list myapp
        hostkit env list myapp --show-secrets
        hostkit --json env list myapp
    """
    service = EnvService()
    formatter = ctx.obj.get("formatter")

    try:
        variables = service.list_env(project, show_secrets=show_secrets)
    except EnvServiceError as e:
        if formatter and formatter.json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    # JSON output mode
    if formatter and formatter.json_mode:
        formatter.success(
            data={
                "project": project,
                "variables": variables,
                "count": len(variables),
                "secrets_shown": show_secrets,
            },
            message=f"Retrieved {len(variables)} environment variable(s)",
        )
        return

    # Pretty output mode
    if not variables:
        click.echo(f"No environment variables set for project '{project}'")
        return

    click.echo(f"Environment variables for '{project}':\n")

    for var in variables:
        secret_indicator = " [secret]" if var["is_secret"] else ""
        click.echo(f"  {var['key']}={var['value']}{secret_indicator}")

    click.echo(f"\nTotal: {len(variables)} variable(s)")
    if not show_secrets:
        click.echo(
            click.style("\nSecrets are redacted. Use --show-secrets to reveal.", fg="yellow")
        )


@env.command("get")
@click.argument("project")
@click.argument("key")
@click.pass_context
@project_access("project")
def get_env(ctx: click.Context, project: str, key: str):
    """Get a specific environment variable.

    Returns the value of a single variable. Useful for scripts or
    checking specific settings.

    Examples:
        hostkit env get myapp DATABASE_URL
        hostkit --json env get myapp PORT
    """
    service = EnvService()
    formatter = ctx.obj.get("formatter")

    try:
        value = service.get_env(project, key)
    except EnvServiceError as e:
        if formatter and formatter.json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    if value is None:
        if formatter and formatter.json_mode:
            formatter.error(
                code="VAR_NOT_FOUND",
                message=f"Environment variable '{key}' not found",
                suggestion=f"Run 'hostkit env list {project}' to see available variables",
            )
        raise click.ClickException(f"Environment variable '{key}' not found")

    # JSON output mode
    if formatter and formatter.json_mode:
        formatter.success(
            data={
                "project": project,
                "key": key,
                "value": value,
            },
            message=f"Retrieved environment variable: {key}",
        )
        return

    # Pretty output mode - just print the value (useful for scripts)
    click.echo(value)


@env.command("set")
@click.argument("project")
@click.argument("key_value")
@click.option("--restart", is_flag=True, help="Restart the service after setting")
@click.pass_context
@project_owner("project")
def set_env(ctx: click.Context, project: str, key_value: str, restart: bool):
    """Set an environment variable.

    Format: KEY=VALUE

    Examples:
        hostkit env set myapp DEBUG=true
        hostkit env set myapp DEBUG=true --restart
        hostkit env set myapp DATABASE_URL="postgresql://user:pass@localhost/db"
        hostkit --json env set myapp DEBUG=true
    """
    formatter = ctx.obj.get("formatter")

    if "=" not in key_value:
        if formatter and formatter.json_mode:
            formatter.error(
                code="INVALID_FORMAT",
                message="Invalid format. Use KEY=VALUE",
                suggestion="Example: hostkit env set myapp DEBUG=true",
            )
        raise click.ClickException("Invalid format. Use KEY=VALUE (e.g., DEBUG=true)")

    key, _, value = key_value.partition("=")
    service = EnvService()

    try:
        result = service.set_env(project, key, value)
    except EnvServiceError as e:
        if formatter and formatter.json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    action = result["action"]

    # Restart service if requested
    restarted = False
    if restart:
        import subprocess

        try:
            subprocess.run(
                ["systemctl", "restart", f"hostkit-{project}"],
                check=True,
                capture_output=True,
            )
            restarted = True
        except subprocess.CalledProcessError:
            pass  # Service might not be running

    # JSON output mode
    if formatter and formatter.json_mode:
        formatter.success(
            data={
                "project": project,
                "key": key,
                "action": action,
                "restarted": restarted,
                "restart_required": not restarted,
            },
            message=f"Environment variable {action}: {key}",
        )
        return

    # Pretty output mode
    click.echo(f"Environment variable {action}: {key}")
    if restarted:
        click.echo(click.style("Service restarted", fg="green"))
    else:
        click.echo(
            click.style(
                "\nRestart the service to apply changes: hostkit service restart " + project,
                fg="yellow",
            )
        )


@env.command("unset")
@click.argument("project")
@click.argument("key")
@click.option("--restart", is_flag=True, help="Restart the service after removing")
@click.pass_context
@project_owner("project")
def unset_env(ctx: click.Context, project: str, key: str, restart: bool):
    """Remove an environment variable.

    Examples:
        hostkit env unset myapp DEBUG
        hostkit env unset myapp DEBUG --restart
        hostkit --json env unset myapp DEBUG
    """
    service = EnvService()
    formatter = ctx.obj.get("formatter")

    try:
        service.unset_env(project, key)
    except EnvServiceError as e:
        if formatter and formatter.json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    # Restart service if requested
    restarted = False
    if restart:
        import subprocess

        try:
            subprocess.run(
                ["systemctl", "restart", f"hostkit-{project}"],
                check=True,
                capture_output=True,
            )
            restarted = True
        except subprocess.CalledProcessError:
            pass  # Service might not be running

    # JSON output mode
    if formatter and formatter.json_mode:
        formatter.success(
            data={
                "project": project,
                "key": key,
                "action": "removed",
                "restarted": restarted,
                "restart_required": not restarted,
            },
            message=f"Environment variable removed: {key}",
        )
        return

    # Pretty output mode
    click.echo(f"Environment variable removed: {key}")
    if restarted:
        click.echo(click.style("Service restarted", fg="green"))
    else:
        click.echo(
            click.style(
                "\nRestart the service to apply changes: hostkit service restart " + project,
                fg="yellow",
            )
        )


@env.command("import")
@click.argument("project")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Overwrite without confirmation")
@click.pass_context
@project_owner("project")
def import_env(ctx: click.Context, project: str, file_path: str, force: bool):
    """Import environment variables from a file.

    WARNING: This REPLACES all existing variables.
    Use 'hostkit env sync' to merge without overwriting.

    Examples:
        hostkit env import myapp ./production.env
        hostkit env import myapp ./production.env --force
    """
    service = EnvService()

    # Check existing variables
    try:
        existing = service.list_env(project, show_secrets=False)
    except EnvServiceError as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    if existing and not force:
        click.echo(
            f"This will replace {len(existing)} existing variable(s) in project '{project}'."
        )
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    try:
        result = service.import_env(project, file_path)
    except EnvServiceError as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    click.echo(f"Imported {result['variables_count']} variable(s) from {result['source']}")
    click.echo(
        click.style(
            "\nRestart the service to apply changes: hostkit service restart " + project,
            fg="yellow",
        )
    )


@env.command("sync")
@click.argument("project")
@click.argument("file_path", type=click.Path(exists=True))
@click.pass_context
@project_owner("project")
def sync_env(ctx: click.Context, project: str, file_path: str):
    """Merge environment variables from a file.

    Only ADDS new variables. Does NOT overwrite existing variables.
    Use 'hostkit env import' to replace all variables.

    Examples:
        hostkit env sync myapp ./defaults.env
    """
    service = EnvService()

    try:
        result = service.sync_env(project, file_path)
    except EnvServiceError as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    click.echo(f"Synced from {result['source']}:")
    click.echo(f"  Added: {result['added_count']} variable(s)")
    click.echo(f"  Skipped (already set): {result['skipped_count']} variable(s)")

    if result["added"]:
        click.echo("\nAdded variables:")
        for key in result["added"]:
            click.echo(f"  - {key}")

    if result["added_count"] > 0:
        click.echo(
            click.style(
                "\nRestart the service to apply changes: hostkit service restart " + project,
                fg="yellow",
            )
        )

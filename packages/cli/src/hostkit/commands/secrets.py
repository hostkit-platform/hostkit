"""Secrets management commands for HostKit."""

import sys

import click

from hostkit.access import project_owner, root_only
from hostkit.services.crypto_service import CryptoServiceError
from hostkit.services.secrets_service import (
    SecretsService,
    SecretsServiceError,
    get_secrets_service,
)


@click.group()
def secrets():
    """Manage encrypted secrets for projects.

    Secrets are stored encrypted at rest using AES-256-GCM.
    Values never appear in logs or command output.
    """
    pass


@secrets.command("init")
@click.option("--force", is_flag=True, help="Regenerate existing master key (WARNING: breaks existing secrets)")
@click.pass_context
@root_only
def init_master_key(ctx: click.Context, force: bool):
    """Initialize the master encryption key.

    This must be run once before using secrets management.
    The master key is stored at /etc/hostkit/master.key with root-only permissions.

    WARNING: Using --force will regenerate the key and make all existing
    encrypted secrets unreadable.

    Examples:
        hostkit secrets init
        hostkit secrets init --force
    """
    service = get_secrets_service()

    if service.master_key_exists() and not force:
        click.echo("Master key already exists.")
        click.echo("Use --force to regenerate (WARNING: existing secrets will be unreadable)")
        return

    if force and service.master_key_exists():
        if not click.confirm(
            click.style(
                "WARNING: This will regenerate the master key. "
                "ALL existing secrets will become unreadable. Continue?",
                fg="red",
            )
        ):
            click.echo("Aborted.")
            return

    try:
        result = service.init_master_key(force=force)
    except CryptoServiceError as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(e.message)

    action = result["action"]
    click.echo(f"Master key {action} at {result['path']}")
    click.echo(click.style("Keep this key secure! Back it up to a safe location.", fg="yellow"))


@secrets.command("list")
@click.argument("project")
@click.pass_context
@project_owner("project")
def list_secrets(ctx: click.Context, project: str):
    """List secrets for a project.

    Shows secret keys and their status (set/not set), but never shows values.

    Examples:
        hostkit secrets list myapp
    """
    service = get_secrets_service()

    try:
        secrets_list = service.list_secrets(project)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    if not secrets_list:
        click.echo(f"No secrets configured for project '{project}'")
        click.echo(
            click.style(
                f"\nTo add a secret: hostkit secrets set {project} KEY_NAME",
                fg="yellow",
            )
        )
        return

    click.echo(f"Secrets for '{project}':\n")
    click.echo(f"{'Key':<30} {'Status':<10} {'Length':<8} {'Required':<10}")
    click.echo("-" * 60)

    for secret in secrets_list:
        key = secret["key"]
        if secret["set"]:
            status = click.style("set", fg="green")
            length = str(secret["length"])
        else:
            status = click.style("not set", fg="red" if secret["required"] else "yellow")
            length = "-"

        required = "required" if secret["required"] else "optional"
        click.echo(f"{key:<30} {status:<18} {length:<8} {required:<10}")

    click.echo(f"\nTotal: {len(secrets_list)} secret(s)")


@secrets.command("set")
@click.argument("project")
@click.argument("key")
@click.option("--stdin", is_flag=True, help="Read value from stdin (for automation)")
@click.option("--required/--optional", default=True, help="Mark secret as required or optional")
@click.option("--provider", help="Provider name (e.g., stripe, google_oauth)")
@click.option("--description", help="Description of the secret")
@click.pass_context
@project_owner("project")
def set_secret(
    ctx: click.Context,
    project: str,
    key: str,
    stdin: bool,
    required: bool,
    provider: str | None,
    description: str | None,
):
    """Set a secret value.

    By default, prompts interactively for the value (hidden input).
    Use --stdin to read from stdin for automation.

    Examples:
        hostkit secrets set myapp STRIPE_API_KEY
        hostkit secrets set myapp STRIPE_API_KEY --provider stripe
        echo "sk_live_xxx" | hostkit secrets set myapp STRIPE_API_KEY --stdin
    """
    service = get_secrets_service()

    # Get value
    if stdin:
        value = sys.stdin.read().strip()
        if not value:
            raise click.ClickException("No value provided via stdin")
    else:
        value = click.prompt(
            f"Enter value for {key}",
            hide_input=True,
            confirmation_prompt=True,
        )

    try:
        result = service.set_secret(
            project=project,
            key=key,
            value=value,
            required=required,
            provider=provider,
            description=description,
        )
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    action = result["action"]
    click.echo(f"Secret {action}: {key} ({result['length']} chars)")


@secrets.command("delete")
@click.argument("project")
@click.argument("key")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def delete_secret(ctx: click.Context, project: str, key: str, force: bool):
    """Delete a secret.

    Examples:
        hostkit secrets delete myapp OLD_API_KEY
        hostkit secrets delete myapp OLD_API_KEY --force
    """
    service = get_secrets_service()

    if not force:
        if not click.confirm(f"Delete secret '{key}' from project '{project}'?"):
            click.echo("Aborted.")
            return

    try:
        service.delete_secret(project, key)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    click.echo(f"Secret deleted: {key}")


@secrets.command("import")
@click.argument("project")
@click.argument("file_path", type=click.Path(exists=True), required=False)
@click.option("--stdin", is_flag=True, help="Read from stdin instead of file")
@click.option("--no-overwrite", is_flag=True, help="Don't overwrite existing secrets")
@click.pass_context
@project_owner("project")
def import_secrets(
    ctx: click.Context,
    project: str,
    file_path: str | None,
    stdin: bool,
    no_overwrite: bool,
):
    """Import secrets from a file or stdin.

    File format should be .env style (KEY=VALUE, one per line).
    Lines starting with # are ignored.

    Examples:
        hostkit secrets import myapp ./secrets.env
        hostkit secrets import myapp --stdin < secrets.env
        cat secrets.env | hostkit secrets import myapp --stdin
    """
    service = get_secrets_service()

    # Read content
    if stdin:
        content = sys.stdin.read()
    elif file_path:
        with open(file_path) as f:
            content = f.read()
    else:
        raise click.ClickException("Provide either a file path or use --stdin")

    # Parse .env format
    secrets_dict = _parse_env_content(content)

    if not secrets_dict:
        click.echo("No secrets found in input")
        return

    try:
        result = service.import_secrets(
            project=project,
            secrets=secrets_dict,
            overwrite=not no_overwrite,
        )
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    click.echo(f"Imported secrets for '{project}':")
    click.echo(f"  Created: {len(result['created'])}")
    click.echo(f"  Updated: {len(result['updated'])}")
    click.echo(f"  Skipped: {len(result['skipped'])}")

    if result["created"]:
        click.echo("\nCreated:")
        for key in result["created"]:
            click.echo(f"  - {key}")

    if result["updated"]:
        click.echo("\nUpdated:")
        for key in result["updated"]:
            click.echo(f"  - {key}")

    if result["skipped"]:
        click.echo("\nSkipped (already exist, use without --no-overwrite to update):")
        for key in result["skipped"]:
            click.echo(f"  - {key}")


@secrets.command("portal")
@click.argument("project")
@click.option(
    "--expires",
    default="24h",
    help="Token expiration (e.g., 1h, 24h, 7d). Default: 24h",
)
@click.option("--revoke", is_flag=True, help="Revoke all existing magic links")
@click.option("--url-only", is_flag=True, help="Output only the magic link URL (for scripting)")
@click.pass_context
@project_owner("project")
def portal(
    ctx: click.Context,
    project: str,
    expires: str,
    revoke: bool,
    url_only: bool,
):
    """Generate or revoke magic links for the secrets portal.

    The magic link provides temporary, scoped access to the secrets portal
    where users can securely enter API keys and other secrets.

    Examples:
        hostkit secrets portal myapp
        hostkit secrets portal myapp --expires 1h
        hostkit secrets portal myapp --expires 7d
        hostkit secrets portal myapp --revoke
        hostkit secrets portal myapp --url-only
    """
    service = get_secrets_service()

    # Handle revocation
    if revoke:
        try:
            result = service.revoke_magic_links(project)
        except (CryptoServiceError, SecretsServiceError) as e:
            formatter = ctx.obj.get("formatter")
            if formatter:
                formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
            raise click.ClickException(e.message)

        click.echo(f"All magic links for '{project}' have been revoked.")
        click.echo(f"Revoked at: {result['revoked_at']}")
        return

    # Parse expiration
    expires_hours = _parse_expiration(expires)
    if expires_hours is None:
        raise click.ClickException(
            f"Invalid expiration format: {expires}. Use formats like '1h', '24h', '7d'"
        )

    # Generate magic link
    try:
        result = service.generate_magic_link(project, expires_hours=expires_hours)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    if url_only:
        click.echo(result["magic_link"])
        return

    click.echo(f"\nSecrets Portal for '{project}'")
    click.echo("=" * 50)
    click.echo(f"\nPortal URL: {result['portal_url']}")
    click.echo(f"\nMagic Link: {click.style(result['magic_link'], fg='cyan')}")
    click.echo(f"\nLink expires in {result['expires_in_hours']} hours.")
    click.echo(f"Expires at: {result['expires_at']}")
    click.echo(
        click.style(
            "\nTo generate a new link (invalidates this one): "
            f"hostkit secrets portal {project} --revoke && hostkit secrets portal {project}",
            fg="yellow",
        )
    )


@secrets.command("clear")
@click.argument("project")
@click.option("--keep-values", is_flag=True, default=True, help="Keep existing secret values (default)")
@click.option("--delete-values", is_flag=True, help="Also delete all secret values")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
@project_owner("project")
def clear_definitions(
    ctx: click.Context,
    project: str,
    keep_values: bool,
    delete_values: bool,
    force: bool,
):
    """Clear all secret definitions for a project.

    This removes all secrets from the portal, allowing you to re-define
    them from a new .env.example file. By default, existing secret VALUES
    are preserved - only the definitions (what shows in the portal) are cleared.

    Use --delete-values to also delete all stored secret values.

    Examples:
        # Clear definitions, keep existing values
        hostkit secrets clear myapp

        # Clear definitions AND delete all values
        hostkit secrets clear myapp --delete-values

        # Skip confirmation
        hostkit secrets clear myapp --force
    """
    service = get_secrets_service()

    # Determine mode
    actually_keep_values = not delete_values

    # Confirmation
    if not force:
        if delete_values:
            msg = f"This will clear all secret definitions AND delete all stored values for '{project}'. Continue?"
        else:
            msg = f"This will clear all secret definitions for '{project}' (values will be preserved). Continue?"

        if not click.confirm(click.style(msg, fg="yellow")):
            click.echo("Aborted.")
            return

    try:
        result = service.clear_definitions(project, keep_values=actually_keep_values)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    click.echo(f"\nCleared secret definitions for '{project}'")

    if result["cleared_definitions"]:
        click.echo(f"\nCleared {len(result['cleared_definitions'])} definition(s):")
        for key in result["cleared_definitions"]:
            click.echo(f"  - {key}")

    if result["preserved_values"]:
        click.echo(click.style(f"\nPreserved {len(result['preserved_values'])} secret value(s)", fg="green"))

    if result["deleted_values"]:
        click.echo(click.style(f"\nDeleted {len(result['deleted_values'])} secret value(s)", fg="red"))

    click.echo(click.style(f"\nRun 'hostkit secrets define {project} --from .env.example' to re-define secrets.", fg="cyan"))


@secrets.command("undefine")
@click.argument("project")
@click.argument("key")
@click.option("--delete-value", is_flag=True, help="Also delete the secret value (if set)")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
@project_owner("project")
def undefine_secret(
    ctx: click.Context,
    project: str,
    key: str,
    delete_value: bool,
    force: bool,
):
    """Remove a secret definition from the portal.

    This removes the secret from showing in the portal. By default,
    if the secret has a value set, it will be preserved. Use --delete-value
    to also delete the stored value.

    Examples:
        # Remove a secret from the portal (keep value if set)
        hostkit secrets undefine myapp OLD_API_KEY

        # Remove from portal AND delete the value
        hostkit secrets undefine myapp OLD_API_KEY --delete-value

        # Skip confirmation
        hostkit secrets undefine myapp OLD_API_KEY --force
    """
    service = get_secrets_service()

    # Confirmation
    if not force:
        if delete_value:
            msg = f"Remove '{key}' from portal AND delete its value?"
        else:
            msg = f"Remove '{key}' from portal? (value will be preserved if set)"

        if not click.confirm(msg):
            click.echo("Aborted.")
            return

    try:
        result = service.undefine_secret(project, key, delete_value=delete_value)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    click.echo(f"Removed '{key}' from portal")

    if result["value_preserved"]:
        click.echo(click.style("  Value preserved (use --delete-value to also remove)", fg="yellow"))
    elif result["value_deleted"]:
        click.echo(click.style("  Value deleted", fg="red"))


@secrets.command("define")
@click.argument("project")
@click.argument("key", required=False)
@click.option("--from", "from_file", type=click.Path(exists=True), help="Parse secrets from .env.example file")
@click.option("--required/--optional", default=True, help="Mark secret as required or optional")
@click.option("--provider", help="Provider ID (e.g., stripe, google_oauth)")
@click.option("--description", help="Description of the secret")
@click.pass_context
@project_owner("project")
def define_secret(
    ctx: click.Context,
    project: str,
    key: str | None,
    from_file: str | None,
    required: bool,
    provider: str | None,
    description: str | None,
):
    """Define required secrets for a project.

    This defines what secrets a project needs, without setting values.
    Use `hostkit secrets verify` to check if all required secrets are set.

    Examples:
        # Define individual secrets
        hostkit secrets define myapp STRIPE_API_KEY --required --provider stripe
        hostkit secrets define myapp SENDGRID_API_KEY --optional

        # Define from .env.example file
        hostkit secrets define myapp --from .env.example
    """
    service = get_secrets_service()

    # Import from file
    if from_file:
        try:
            with open(from_file) as f:
                content = f.read()

            result = service.define_secrets_from_env(project, content)
        except (CryptoServiceError, SecretsServiceError) as e:
            formatter = ctx.obj.get("formatter")
            if formatter:
                formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
            raise click.ClickException(e.message)

        click.echo(f"Defined secrets for '{project}' from {from_file}:\n")

        if result["defined"]:
            click.echo("Defined:")
            for item in result["defined"]:
                req_str = click.style("required", fg="yellow") if item["required"] else "optional"
                provider_str = f" ({item['provider']})" if item["provider"] else ""
                click.echo(f"  - {item['key']}: {req_str}{provider_str}")

        if result["auto_generated"]:
            click.echo(f"\nSkipped (auto-generated): {', '.join(result['auto_generated'])}")

        if result["skipped"]:
            click.echo(f"\nSkipped (errors): {', '.join(result['skipped'])}")

        click.echo(f"\nTotal: {result['total_defined']} secret(s) defined")
        click.echo(click.style(f"\nRun 'hostkit secrets portal {project}' to generate a link for setting values.", fg="cyan"))
        return

    # Define single key
    if not key:
        raise click.ClickException("Either provide a KEY or use --from to import from a file")

    try:
        result = service.define_secret(
            project=project,
            key=key,
            required=required,
            provider=provider,
            description=description,
        )
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    req_str = "required" if required else "optional"
    provider_str = f" (provider: {result['provider']})" if result["provider"] else ""
    click.echo(f"Secret {result['action']}: {key} ({req_str}){provider_str}")


@secrets.command("verify")
@click.argument("project")
@click.option("--json", "output_json", is_flag=True, help="Output in JSON format")
@click.pass_context
@project_owner("project")
def verify_secrets(ctx: click.Context, project: str, output_json: bool):
    """Verify all required secrets are set.

    Checks that all required secrets have values and validates formats
    for known key types. Use before deployment to ensure configuration
    is complete.

    Exit codes:
        0 - All required secrets are set
        1 - Missing required secrets

    Examples:
        hostkit secrets verify myapp
        hostkit secrets verify myapp --json
    """
    service = get_secrets_service()

    try:
        result = service.verify_secrets(project)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    if output_json:
        import json
        click.echo(json.dumps(result, indent=2))
        if not result["ready"]:
            ctx.exit(1)
        return

    click.echo(f"\nSecrets verification for '{project}'")
    click.echo("=" * 50)

    if not result["secrets"]:
        click.echo("\nNo secrets defined for this project.")
        click.echo(click.style(f"Use 'hostkit secrets define {project} --from .env.example' to define requirements.", fg="yellow"))
        return

    # Display each secret
    for secret in result["secrets"]:
        key = secret["key"]
        is_set = secret["set"]
        required = secret["required"]
        length = secret["length"]
        format_valid = secret.get("format_valid")
        key_type = secret.get("key_type")
        warnings = secret.get("warnings", [])

        # Build status string
        if is_set:
            if format_valid is True:
                status_icon = click.style("✓", fg="green")
                status_text = f"({length} chars, valid format)"
            elif format_valid is False:
                status_icon = click.style("⚠", fg="yellow")
                status_text = f"({length} chars, invalid format)"
            else:
                status_icon = click.style("✓", fg="green")
                status_text = f"({length} chars)"

            # Add key type indicator
            if key_type == "test":
                status_text += click.style(" [TEST KEY]", fg="yellow")
            elif key_type == "live":
                status_text += click.style(" [LIVE]", fg="green")
        else:
            if required:
                status_icon = click.style("✗", fg="red")
                status_text = click.style("not set", fg="red")
            else:
                status_icon = click.style("○", fg="white")
                status_text = "not set (optional)"

        req_label = click.style("[required]", fg="yellow") if required else "[optional]"
        click.echo(f"\n{status_icon} {key} {req_label}")
        click.echo(f"  Status: {status_text}")

        # Show warnings
        for warning in warnings:
            click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))

        # Show description if available
        if secret.get("description"):
            click.echo(f"  Description: {secret['description']}")

        # Show provider info
        if secret.get("provider_name"):
            click.echo(f"  Provider: {secret['provider_name']}")
            if secret.get("provider_url"):
                click.echo(f"  Get key from: {secret['provider_url']}")

    # Summary
    click.echo("\n" + "-" * 50)
    click.echo(f"Required: {result['required_set']}/{result['required_count']}")
    click.echo(f"Optional: {result['optional_set']}/{result['optional_count']}")

    if result["ready"]:
        if result["has_warnings"]:
            click.echo(click.style("\n⚠ Ready to deploy (with warnings)", fg="yellow"))
        else:
            click.echo(click.style("\n✓ Ready to deploy", fg="green"))
    else:
        missing = result["required_count"] - result["required_set"]
        click.echo(click.style(f"\n✗ Not ready: {missing} required secret(s) missing", fg="red"))
        click.echo(click.style(f"Set via portal: hostkit secrets portal {project}", fg="cyan"))
        ctx.exit(1)


@secrets.command("audit")
@click.argument("project")
@click.option("--limit", default=50, help="Maximum number of entries to show")
@click.option("--json", "output_json", is_flag=True, help="Output in JSON format")
@click.pass_context
@project_owner("project")
def audit_secrets(ctx: click.Context, project: str, limit: int, output_json: bool):
    """View secrets audit log for a project.

    Shows recent secrets operations including portal access, secret updates,
    and magic link generation/revocation.

    Examples:
        hostkit secrets audit myapp
        hostkit secrets audit myapp --limit 100
        hostkit secrets audit myapp --json
    """
    service = get_secrets_service()

    try:
        entries = service.get_audit_log(project, limit=limit)
    except (CryptoServiceError, SecretsServiceError) as e:
        formatter = ctx.obj.get("formatter")
        if formatter:
            formatter.error(code=e.code, message=e.message, suggestion=getattr(e, "suggestion", None))
        raise click.ClickException(e.message)

    if output_json:
        import json
        click.echo(json.dumps(entries, indent=2))
        return

    if not entries:
        click.echo(f"No audit log entries for project '{project}'")
        return

    click.echo(f"\nAudit log for '{project}' (most recent first)")
    click.echo("=" * 70)

    for entry in entries:
        timestamp = entry.get("timestamp", "")[:19].replace("T", " ")
        action = entry.get("action", "unknown")
        details = entry.get("details", {})
        ip_address = entry.get("ip_address", "")

        # Format action with color
        action_colors = {
            "secret.created": "green",
            "secret.updated": "green",
            "secret.deleted": "red",
            "secrets.imported": "cyan",
            "secrets.injected": "cyan",
            "secrets.deleted_all": "red",
            "secret.defined": "blue",
            "magic_link.generated": "yellow",
            "magic_links.revoked_all": "red",
            "portal.secrets_viewed": "white",
            "portal.secrets_updated": "green",
        }
        action_color = action_colors.get(action, "white")
        action_str = click.style(action, fg=action_color)

        click.echo(f"\n{timestamp}  {action_str}")

        # Show relevant details
        if ip_address:
            click.echo(f"  IP: {ip_address}")

        if details:
            # Format common detail fields
            if "key" in details:
                click.echo(f"  Key: {details['key']}")
            if "keys" in details:
                click.echo(f"  Keys: {', '.join(details['keys'])}")
            if "injected" in details and details["injected"]:
                click.echo(f"  Injected: {', '.join(details['injected'])}")
            if "created" in details and details["created"]:
                click.echo(f"  Created: {', '.join(details['created'])}")
            if "updated" in details and details["updated"]:
                click.echo(f"  Updated: {', '.join(details['updated'])}")
            if "expires_at" in details:
                click.echo(f"  Expires: {details['expires_at']}")
            if "jti" in details:
                click.echo(f"  Token ID: {details['jti'][:16]}...")

    click.echo(f"\n\nShowing {len(entries)} of {limit} max entries")


def _parse_expiration(expires: str) -> int | None:
    """Parse expiration string to hours.

    Args:
        expires: String like '1h', '24h', '7d'

    Returns:
        Hours, or None if invalid format
    """
    expires = expires.strip().lower()

    if expires.endswith("h"):
        try:
            return int(expires[:-1])
        except ValueError:
            return None
    elif expires.endswith("d"):
        try:
            return int(expires[:-1]) * 24
        except ValueError:
            return None
    else:
        # Try parsing as plain hours
        try:
            return int(expires)
        except ValueError:
            return None


def _parse_env_content(content: str) -> dict[str, str]:
    """Parse .env file content into a dictionary.

    Handles:
    - KEY=VALUE
    - KEY="VALUE WITH SPACES"
    - KEY='VALUE WITH SPACES'
    - Comments (# ...)
    - Empty lines
    """
    env_vars: dict[str, str] = {}

    for line in content.splitlines():
        line = line.strip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue

        # Skip export prefix if present
        if line.startswith("export "):
            line = line[7:]

        # Split on first = sign
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()

        if not key:
            continue

        # Remove surrounding quotes if present
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        env_vars[key] = value

    return env_vars

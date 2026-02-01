"""Operator management commands for HostKit.

Operators are specialized users (typically AI agents) that can autonomously
manage VPS deployments with controlled sudo access to hostkit commands.
"""

from pathlib import Path

import click
import yaml

from hostkit.access import root_only
from hostkit.config import get_config, reload_config
from hostkit.output import OutputFormatter
from hostkit.services.operator_service import OperatorService, OperatorServiceError


@click.group("operator")
@click.pass_context
def operator(ctx: click.Context) -> None:
    """Manage HostKit operators (AI agent users).

    Operators are users with controlled sudo access to hostkit commands,
    enabling AI agents to autonomously deploy and manage applications.
    """
    pass


@operator.command("setup")
@click.option(
    "--user", "-u",
    default=None,
    help="Operator username (default: ai-operator)",
)
@click.pass_context
@root_only
def setup(ctx: click.Context, user: str | None) -> None:
    """Create and configure an operator user.

    Creates a Linux user with:
    - Home directory at /home/<username>
    - SSH directory for key-based authentication
    - Sudoers rules for passwordless hostkit access

    Example:
        hostkit operator setup
        hostkit operator setup --user claude-agent
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = OperatorService()

    try:
        operator = service.setup(username=user)

        formatter.success(
            message=f"Operator '{operator.username}' created successfully",
            data={
                "username": operator.username,
                "home": f"/home/{operator.username}",
                "created_at": operator.created_at,
            },
        )

        # Print next steps
        if not formatter.json_mode:
            click.echo()
            click.echo("Next steps:")
            click.echo("  1. Add SSH key: hostkit operator add-key --github <username>")
            click.echo("  2. Test access:  hostkit operator test")
            click.echo()
            click.echo("The operator can then connect via:")
            click.echo(f"  ssh {operator.username}@<server-ip>")
            click.echo("  sudo hostkit <command>")

    except OperatorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@operator.command("add-key")
@click.argument("key", required=False)
@click.option(
    "--user", "-u",
    default=None,
    help="Operator username (default: ai-operator)",
)
@click.option(
    "--github", "-g",
    default=None,
    help="Fetch SSH keys from GitHub username",
)
@click.option(
    "--file", "-f",
    "file_path",
    default=None,
    type=click.Path(exists=True),
    help="Read SSH key from file",
)
@click.pass_context
@root_only
def add_key(
    ctx: click.Context,
    key: str | None,
    user: str | None,
    github: str | None,
    file_path: str | None,
) -> None:
    """Add an SSH public key to an operator.

    Supports multiple key sources:
    - Direct key as argument
    - Fetch from GitHub user's public keys
    - Read from a file

    Examples:
        hostkit operator add-key "ssh-ed25519 AAAA... user@host"
        hostkit operator add-key --github myuser
        hostkit operator add-key --file ~/.ssh/id_ed25519.pub
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = OperatorService()

    try:
        result = service.add_key(
            username=user,
            key=key,
            github_user=github,
            file_path=file_path,
        )

        formatter.success(
            message=f"Added {result['keys_added']} SSH key(s) to operator '{result['username']}'",
            data=result,
        )

    except OperatorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@operator.command("test")
@click.option(
    "--user", "-u",
    default=None,
    help="Operator username (default: ai-operator)",
)
@click.pass_context
@root_only
def test(ctx: click.Context, user: str | None) -> None:
    """Test operator access configuration.

    Verifies that the operator is properly configured:
    - Linux user exists
    - SSH directory and authorized_keys
    - Sudoers rules
    - Sudo access to hostkit
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = OperatorService()

    try:
        results = service.test_access(username=user)

        if formatter.json_mode:
            formatter.success(message="Operator test complete", data=results)
        else:
            click.echo(f"Operator: {results['username']}")
            click.echo()

            for check_name, check_result in results["checks"].items():
                status = check_result["status"]
                message = check_result["message"]

                if status == "pass":
                    icon = click.style("[PASS]", fg="green")
                elif status == "fail":
                    icon = click.style("[FAIL]", fg="red")
                elif status == "warn":
                    icon = click.style("[WARN]", fg="yellow")
                else:
                    icon = click.style("[SKIP]", fg="blue")

                # Format check name nicely
                check_display = check_name.replace("_", " ").title()
                click.echo(f"  {icon} {check_display}: {message}")

            click.echo()
            if results["overall"]:
                click.echo(click.style("Overall: PASSED", fg="green", bold=True))
            else:
                click.echo(click.style("Overall: FAILED", fg="red", bold=True))
                click.echo()
                click.echo("Fix the failing checks and run test again.")

    except OperatorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@operator.command("revoke")
@click.option(
    "--user", "-u",
    default=None,
    help="Operator username (default: ai-operator)",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Skip confirmation",
)
@click.pass_context
@root_only
def revoke(ctx: click.Context, user: str | None, force: bool) -> None:
    """Revoke an operator's access and remove the user.

    This will:
    - Remove sudoers rules
    - Delete the Linux user and home directory
    - Remove from the HostKit database

    WARNING: This is destructive and cannot be undone.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = OperatorService()

    # Determine the username
    username = user or "ai-operator"

    if not force:
        click.echo(f"This will permanently remove operator '{username}' and their home directory.")
        if not click.confirm("Are you sure you want to continue?"):
            click.echo("Aborted.")
            raise SystemExit(0)

    try:
        result = service.revoke(username=user)

        formatter.success(
            message=result["message"],
            data=result,
        )

    except OperatorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@operator.command("list")
@click.pass_context
@root_only
def list_operators(ctx: click.Context) -> None:
    """List all registered operators."""
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = OperatorService()

    operators = service.list_operators()

    if formatter.json_mode:
        formatter.success(
            message="Operators retrieved",
            data={
                "operators": [
                    {
                        "username": op.username,
                        "ssh_keys_count": len(op.ssh_keys),
                        "created_at": op.created_at,
                        "last_login": op.last_login,
                    }
                    for op in operators
                ],
                "count": len(operators),
            },
        )
    else:
        if not operators:
            click.echo("No operators registered.")
            click.echo()
            click.echo("Create one with: hostkit operator setup")
            return

        click.echo(f"Operators ({len(operators)}):")
        click.echo()

        for op in operators:
            click.echo(f"  {op.username}")
            click.echo(f"    SSH Keys: {len(op.ssh_keys)}")
            click.echo(f"    Created:  {op.created_at}")
            if op.last_login:
                click.echo(f"    Last Login: {op.last_login}")
            click.echo()


@operator.command("set-project-key")
@click.argument("key", required=False)
@click.option(
    "--file", "-f",
    "file_path",
    default=None,
    type=click.Path(exists=True),
    help="Read SSH key from file",
)
@click.option(
    "--github", "-g",
    default=None,
    help="Fetch SSH keys from GitHub username",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clear all operator project keys",
)
@click.pass_context
@root_only
def set_project_key(
    ctx: click.Context,
    key: str | None,
    file_path: str | None,
    github: str | None,
    clear: bool,
) -> None:
    """Set SSH key(s) to auto-add to all new projects.

    When new projects are created, these keys are automatically added
    to the project's authorized_keys, allowing operator access.

    Examples:
        hostkit operator set-project-key "ssh-ed25519 AAAA... user@host"
        hostkit operator set-project-key --file ~/.ssh/id_ed25519.pub
        hostkit operator set-project-key --github myuser
        hostkit operator set-project-key --clear
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    config_path = Path("/etc/hostkit/config.yaml")

    # Load existing config
    config_data: dict = {}
    if config_path.exists():
        try:
            config_data = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError:
            pass

    if clear:
        config_data["operator_ssh_keys"] = []
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.dump(config_data, default_flow_style=False))
        reload_config()
        formatter.success(message="Cleared all operator project keys", data={"keys": []})
        return

    # Collect keys to add
    keys_to_add: list[str] = []

    if key:
        keys_to_add.append(key.strip())

    if file_path:
        keys_to_add.append(Path(file_path).read_text().strip())

    if github:
        import requests
        url = f"https://github.com/{github}.keys"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            formatter.error(
                code="GITHUB_USER_NOT_FOUND",
                message=f"GitHub user '{github}' not found",
                suggestion="Check the username and try again",
            )
            raise SystemExit(1)
        resp.raise_for_status()
        for line in resp.text.strip().split("\n"):
            if line.strip():
                keys_to_add.append(line.strip())

    if not keys_to_add:
        formatter.error(
            code="NO_KEY_PROVIDED",
            message="No SSH key provided",
            suggestion="Use --file, --github, or provide key as argument",
        )
        raise SystemExit(1)

    # Validate keys
    import subprocess
    valid_keys = []
    for k in keys_to_add:
        result = subprocess.run(
            ["ssh-keygen", "-lf", "-"],
            input=k.encode(),
            capture_output=True,
        )
        if result.returncode == 0:
            valid_keys.append(k)
        else:
            click.echo(f"Warning: Skipping invalid key: {k[:50]}...")

    if not valid_keys:
        formatter.error(
            code="NO_VALID_KEYS",
            message="No valid SSH keys found",
            suggestion="Ensure the key format is correct",
        )
        raise SystemExit(1)

    # Add to config (replace existing keys)
    config_data["operator_ssh_keys"] = valid_keys
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config_data, default_flow_style=False))
    reload_config()

    formatter.success(
        message=f"Set {len(valid_keys)} operator project key(s)",
        data={"keys_count": len(valid_keys)},
    )

    if not formatter.json_mode:
        click.echo()
        click.echo("These keys will be automatically added to all new projects.")
        click.echo("Use 'hostkit operator show-project-keys' to view them.")


@operator.command("show-project-keys")
@click.pass_context
@root_only
def show_project_keys(ctx: click.Context) -> None:
    """Show SSH keys that are auto-added to new projects."""
    formatter: OutputFormatter = ctx.obj["formatter"]
    config = get_config()

    keys = config.operator_ssh_keys

    if formatter.json_mode:
        formatter.success(
            message="Operator project keys",
            data={"keys": keys, "count": len(keys)},
        )
    else:
        if not keys:
            click.echo("No operator project keys configured.")
            click.echo()
            click.echo("Set one with: hostkit operator set-project-key --file ~/.ssh/id_ed25519.pub")
            return

        click.echo(f"Operator project keys ({len(keys)}):")
        click.echo()
        for i, k in enumerate(keys, 1):
            # Get fingerprint for display
            import subprocess
            result = subprocess.run(
                ["ssh-keygen", "-lf", "-"],
                input=k.encode(),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                fingerprint = parts[1] if len(parts) > 1 else "unknown"
                comment = " ".join(parts[2:-1]) if len(parts) > 3 else ""
                key_type = parts[-1].strip("()") if parts else ""
                click.echo(f"  {i}. {fingerprint}")
                if comment:
                    click.echo(f"     Comment: {comment}")
                if key_type:
                    click.echo(f"     Type: {key_type}")
            else:
                click.echo(f"  {i}. {k[:60]}...")
            click.echo()


@operator.command("sync-project-keys")
@click.pass_context
@root_only
def sync_project_keys(ctx: click.Context) -> None:
    """Add operator project keys to all existing projects.

    Use this after setting operator project keys to retroactively
    add them to projects that were created before the keys were set.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    config = get_config()

    keys = config.operator_ssh_keys
    if not keys:
        formatter.error(
            code="NO_KEYS_CONFIGURED",
            message="No operator project keys configured",
            suggestion="First set keys with: hostkit operator set-project-key",
        )
        raise SystemExit(1)

    from hostkit.database import get_db
    from hostkit.services import ssh_service

    db = get_db()
    projects = db.list_projects()

    results = {"added": [], "skipped": [], "errors": []}

    for project in projects:
        project_name = project["name"]
        ssh_service.ensure_ssh_dir(project_name)

        for key in keys:
            try:
                ssh_service.add_key(project_name, key)
                results["added"].append(project_name)
            except ValueError as e:
                if "already exists" in str(e):
                    results["skipped"].append(project_name)
                else:
                    results["errors"].append({"project": project_name, "error": str(e)})

    # Deduplicate
    results["added"] = list(set(results["added"]))
    results["skipped"] = list(set(results["skipped"]))

    if formatter.json_mode:
        formatter.success(
            message="Sync complete",
            data=results,
        )
    else:
        click.echo(f"Added keys to {len(results['added'])} project(s)")
        if results["skipped"]:
            click.echo(f"Skipped {len(results['skipped'])} project(s) (keys already present)")
        if results["errors"]:
            click.echo(f"Errors in {len(results['errors'])} project(s):")
            for err in results["errors"]:
                click.echo(f"  - {err['project']}: {err['error']}")

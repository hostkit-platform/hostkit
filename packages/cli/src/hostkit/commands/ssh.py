"""SSH management commands for HostKit."""

import click

from hostkit.access import get_access_context, project_access, root_only
from hostkit.services import ssh_service


@click.group()
def ssh():
    """Manage SSH access for projects."""
    pass


@ssh.command("add-key")
@click.argument("project")
@click.argument("public_key", required=False)
@click.option("--github", "github_user", help="Fetch keys from GitHub username")
@click.option(
    "--file", "key_file", type=click.Path(exists=True), help="Read key from file"
)
@project_access("project")
def add_key(
    project: str,
    public_key: str | None,
    github_user: str | None,
    key_file: str | None,
):
    """Add an SSH public key to a project.

    Examples:
        hostkit ssh add-key myapp "ssh-ed25519 AAAA... user@host"
        hostkit ssh add-key myapp --github octocat
        hostkit ssh add-key myapp --file ~/.ssh/id_ed25519.pub
    """
    keys_to_add = []

    if github_user:
        try:
            keys_to_add = ssh_service.fetch_github_keys(github_user)
            click.echo(f"Found {len(keys_to_add)} key(s) for GitHub user '{github_user}'")
        except ValueError as e:
            raise click.ClickException(str(e))
    elif key_file:
        with open(key_file) as f:
            keys_to_add = [f.read().strip()]
    elif public_key:
        keys_to_add = [public_key]
    else:
        raise click.ClickException(
            "Must provide a public key, --github username, or --file path"
        )

    added = 0
    for key in keys_to_add:
        try:
            result = ssh_service.add_key(project, key)
            click.echo(f"Added key: {result.fingerprint} ({result.key_type})")
            added += 1
        except ValueError as e:
            click.echo(f"Skipped: {e}", err=True)

    if added == 0:
        raise click.ClickException("No keys were added")

    click.echo(f"\nSuccessfully added {added} key(s) to project '{project}'")


@ssh.command("remove-key")
@click.argument("project")
@click.argument("fingerprint")
@project_access("project")
def remove_key(project: str, fingerprint: str):
    """Remove an SSH key by fingerprint.

    Use 'hostkit ssh list-keys' to see fingerprints.

    Examples:
        hostkit ssh remove-key myapp SHA256:abc123...
        hostkit ssh remove-key myapp abc123...
    """
    try:
        ssh_service.remove_key(project, fingerprint)
        click.echo(f"Removed key: {fingerprint}")
    except ValueError as e:
        raise click.ClickException(str(e))


@ssh.command("list-keys")
@click.argument("project")
@project_access("project")
def list_keys(project: str):
    """List all authorized SSH keys for a project."""
    keys = ssh_service.list_keys(project)

    if not keys:
        click.echo(f"No SSH keys configured for project '{project}'")
        return

    click.echo(f"SSH keys for project '{project}':\n")
    for key in keys:
        comment = f" ({key.comment})" if key.comment else ""
        click.echo(f"  {key.fingerprint} {key.key_type}{comment}")

    click.echo(f"\nTotal: {len(keys)} key(s)")


@ssh.command("sessions")
@click.argument("project")
@project_access("project")
def sessions(project: str):
    """Show active SSH sessions for a project."""
    active = ssh_service.get_sessions(project)

    if not active:
        click.echo(f"No active SSH sessions for project '{project}'")
        return

    click.echo(f"Active SSH sessions for '{project}':\n")
    for s in active:
        from_info = f" from {s.from_host}" if s.from_host else ""
        click.echo(f"  {s.tty}  {s.login_time}{from_info}")

    click.echo(f"\nTotal: {len(active)} session(s)")


@ssh.command("kick")
@click.argument("project")
@click.argument("session_id", required=False)
@click.option("--all", "kick_all", is_flag=True, help="Kick all sessions")
@project_access("project")
def kick(project: str, session_id: str | None, kick_all: bool):
    """Kick SSH sessions for a project.

    Examples:
        hostkit ssh kick myapp pts/0
        hostkit ssh kick myapp --all
    """
    if kick_all:
        count = ssh_service.kick_all_sessions(project)
        if count == 0:
            click.echo(f"No active sessions to kick for project '{project}'")
        else:
            click.echo(f"Kicked {count} session(s) for project '{project}'")
    elif session_id:
        if ssh_service.kick_session(session_id):
            click.echo(f"Kicked session: {session_id}")
        else:
            raise click.ClickException(f"Failed to kick session: {session_id}")
    else:
        raise click.ClickException("Must provide a session ID or --all flag")


@ssh.command("enable")
@click.argument("project")
@root_only
def enable(project: str):
    """Enable SSH access for a project.

    This restores the user's shell to /bin/bash, allowing SSH logins.

    Example:
        hostkit ssh enable myapp
    """
    try:
        was_disabled = ssh_service.enable_ssh(project)
        if was_disabled:
            click.echo(f"SSH access enabled for project '{project}'")
        else:
            click.echo(f"SSH access was already enabled for project '{project}'")
    except ValueError as e:
        raise click.ClickException(str(e))
    except RuntimeError as e:
        raise click.ClickException(str(e))


@ssh.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Kick all active sessions first")
@root_only
def disable(project: str, force: bool):
    """Disable SSH access for a project.

    This changes the user's shell to /sbin/nologin, preventing SSH logins.

    Examples:
        hostkit ssh disable myapp
        hostkit ssh disable myapp --force
    """
    try:
        kicked = ssh_service.disable_ssh(project, force=force)
        if kicked:
            click.echo(f"Kicked {kicked} active session(s)")
        click.echo(f"SSH access disabled for project '{project}'")
    except ValueError as e:
        raise click.ClickException(str(e))
    except RuntimeError as e:
        raise click.ClickException(str(e))


@ssh.command("status")
@click.argument("project", required=False)
def status(project: str | None):
    """Show SSH status for a project or all projects.

    Examples:
        hostkit ssh status myapp
        hostkit ssh status
    """
    ctx = get_access_context()

    if project:
        # Check access for specific project
        if not ctx.is_root and not (ctx.is_project_user and ctx.project == project):
            raise click.ClickException(
                f"Access denied: must be root or user '{project}'"
            )

        try:
            s = ssh_service.get_ssh_status(project)
            status_str = (
                click.style("enabled", fg="green")
                if s["enabled"]
                else click.style("disabled", fg="red")
            )
            click.echo(f"SSH Status for '{project}':\n")
            click.echo(f"  Status:           {status_str}")
            click.echo(f"  Shell:            {s['shell']}")
            click.echo(f"  Active sessions:  {s['active_sessions']}")
            click.echo(f"  Authorized keys:  {s['authorized_keys']}")
        except ValueError as e:
            raise click.ClickException(str(e))
    else:
        # Show all projects - requires root
        if not ctx.is_root:
            raise click.ClickException("Must be root to view all projects")

        statuses = ssh_service.get_all_projects_ssh_status()

        if not statuses:
            click.echo("No project users found")
            return

        click.echo("SSH Status for all projects:\n")
        click.echo(f"  {'Project':<20} {'Status':<10} {'Sessions':<10} {'Keys':<6}")
        click.echo("  " + "-" * 50)

        for s in statuses:
            status_str = (
                click.style("enabled", fg="green")
                if s["enabled"]
                else click.style("disabled", fg="red")
            )
            click.echo(
                f"  {s['project']:<20} {status_str:<19} "
                f"{s['active_sessions']:<10} {s['authorized_keys']:<6}"
            )

        click.echo(f"\nTotal: {len(statuses)} project(s)")

"""Provision command for one-command project setup."""

from pathlib import Path

import click

from hostkit.access import operator_or_root
from hostkit.services.provision_service import ProvisionService, ProvisionServiceError


@click.command("provision")
@click.argument("name")
@click.option(
    "--runtime",
    type=click.Choice(["python", "node", "nextjs", "static"]),
    default="python",
    help="Runtime environment for the project",
)
@click.option(
    "--with-db",
    is_flag=True,
    help="Create a PostgreSQL database for the project",
)
@click.option(
    "--with-auth",
    is_flag=True,
    help="Enable authentication service",
)
@click.option(
    "--with-secrets",
    is_flag=True,
    help="Inject secrets from vault into .env",
)
@click.option(
    "--ssh-key",
    "ssh_keys",
    multiple=True,
    help="SSH public key for project user access (can be used multiple times)",
)
@click.option(
    "--github-user",
    "github_users",
    multiple=True,
    help="GitHub username to fetch SSH keys from (can be used multiple times)",
)
@click.option(
    "--domain",
    default=None,
    help="Domain name to configure in Nginx",
)
@click.option(
    "--dev-domain",
    is_flag=True,
    help="Use nip.io development domain (e.g., project.<VPS_IP>.nip.io)",
)
@click.option(
    "--ssl",
    is_flag=True,
    help="Provision SSL certificate (requires --domain)",
)
@click.option(
    "--ssl-email",
    default=None,
    help="Admin email for Let's Encrypt SSL registration",
)
@click.option(
    "--source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Source directory to deploy",
)
@click.option(
    "--no-install",
    is_flag=True,
    help="Skip dependency installation during deploy",
)
@click.option(
    "--no-start",
    is_flag=True,
    help="Don't start the service after provisioning",
)
@click.pass_context
@operator_or_root
def provision(
    ctx: click.Context,
    name: str,
    runtime: str,
    with_db: bool,
    with_auth: bool,
    with_secrets: bool,
    ssh_keys: tuple[str, ...],
    github_users: tuple[str, ...],
    domain: str | None,
    dev_domain: bool,
    ssl: bool,
    ssl_email: str | None,
    source: Path | None,
    no_install: bool,
    no_start: bool,
) -> None:
    """Provision a complete project with all supporting services.

    Creates a new project with optional database, authentication,
    domain configuration, SSL, and initial deployment in one command.

    Examples:

        # Simple project
        hostkit provision myapp

        # Python project with database and auth
        hostkit provision myapp --runtime python --with-db --with-auth

        # Full setup with domain and SSL
        hostkit provision myapp --with-db --domain myapp.example.com --ssl

        # Deploy from source directory
        hostkit provision myapp --source ./output/myapp --with-db

        # Development domain (uses nip.io)
        hostkit provision myapp --dev-domain

        # With SSH access for project user (direct key)
        hostkit provision myapp --ssh-key "ssh-ed25519 AAAA... user@host"

        # With SSH access from GitHub
        hostkit provision myapp --github-user projectowner

        # Multiple SSH keys/users
        hostkit provision myapp --github-user alice --github-user bob
    """
    formatter = ctx.obj.get("formatter")
    json_mode = ctx.obj.get("json_mode", False)

    # Determine domain to use
    actual_domain = domain
    if dev_domain and not domain:
        # Use nip.io development domain
        from hostkit.config import get_config
        vps_ip = get_config().vps_ip
        actual_domain = f"{name}.{vps_ip}.nip.io"

    # Validate SSL requirement
    if ssl and not actual_domain:
        if formatter and json_mode:
            formatter.error(
                code="SSL_REQUIRES_DOMAIN",
                message="SSL provisioning requires a domain",
                suggestion="Use --domain or --dev-domain to specify a domain",
            )
            return
        raise click.ClickException(
            "SSL provisioning requires a domain. Use --domain or --dev-domain."
        )

    provision_service = ProvisionService()

    if not json_mode:
        click.echo(f"\nProvisioning project '{name}'...\n")

        # Show what will be created
        steps = ["Create project"]
        if with_db:
            steps.append("Create PostgreSQL database")
        if with_auth:
            steps.append("Enable authentication service")
        if with_secrets:
            steps.append("Inject secrets from vault")
        if ssh_keys or github_users:
            key_sources = []
            if ssh_keys:
                key_sources.append(f"{len(ssh_keys)} direct key(s)")
            if github_users:
                key_sources.append(f"GitHub: {', '.join(github_users)}")
            steps.append(f"Add SSH keys ({', '.join(key_sources)})")
        if actual_domain:
            steps.append(f"Configure domain: {actual_domain}")
        if ssl:
            steps.append("Provision SSL certificate")
        if source:
            steps.append(f"Deploy from: {source}")
        if not no_start and runtime != "static":
            steps.append("Start service")

        click.echo("Steps to execute:")
        for i, step in enumerate(steps, 1):
            click.echo(f"  {i}. {step}")
        click.echo()

    try:
        result = provision_service.provision(
            name=name,
            runtime=runtime,
            with_db=with_db,
            with_auth=with_auth,
            with_secrets=with_secrets,
            ssh_keys=list(ssh_keys) if ssh_keys else None,
            github_users=list(github_users) if github_users else None,
            domain=actual_domain,
            ssl=ssl,
            ssl_email=ssl_email,
            source=source,
            install_deps=not no_install,
            start=not no_start,
        )

        if json_mode and formatter:
            if result.success:
                formatter.success(
                    data=result.to_dict(),
                    message=f"Project '{name}' provisioned successfully",
                )
            else:
                formatter.error(
                    code="PROVISION_PARTIAL",
                    message=result.error or "Some provisioning steps failed",
                    suggestion=result.suggestion,
                )
        else:
            # Pretty output
            click.echo()
            if result.success:
                click.echo(
                    click.style(
                        f"✓ Project '{name}' provisioned successfully!",
                        fg="green",
                        bold=True,
                    )
                )
            else:
                click.echo(
                    click.style(
                        f"⚠ Project '{name}' partially provisioned",
                        fg="yellow",
                        bold=True,
                    )
                )

            click.echo()

            # Show results
            click.echo("Results:")
            click.echo(f"  Port: {result.port}")
            click.echo(f"  Runtime: {result.runtime}")

            if result.database_created:
                click.echo(
                    click.style(f"  ✓ Database: {result.database_name}", fg="green")
                )

            if result.auth_enabled:
                click.echo(
                    click.style(f"  ✓ Auth service: port {result.auth_port}", fg="green")
                )

            if result.secrets_injected:
                click.echo(
                    click.style(
                        f"  ✓ Secrets: {result.secrets_count} injected", fg="green"
                    )
                )
            elif with_secrets and not result.secrets_injected:
                click.echo(click.style("  ✗ Secrets: failed to inject", fg="red"))

            if result.ssh_keys_added > 0:
                click.echo(
                    click.style(
                        f"  ✓ SSH keys: {result.ssh_keys_added} added", fg="green"
                    )
                )
            if result.ssh_keys_failed:
                for failed in result.ssh_keys_failed:
                    click.echo(click.style(f"  ✗ SSH key failed: {failed}", fg="red"))
            elif (ssh_keys or github_users) and result.ssh_keys_added == 0:
                click.echo(click.style("  ✗ SSH keys: none added", fg="red"))

            if result.domain_configured:
                click.echo(
                    click.style(f"  ✓ Domain: {result.domain_configured}", fg="green")
                )

            if result.ssl_provisioned:
                click.echo(click.style("  ✓ SSL: provisioned", fg="green"))
            elif ssl and not result.ssl_provisioned:
                click.echo(click.style("  ✗ SSL: failed", fg="red"))

            if result.deployed:
                click.echo(
                    click.style(f"  ✓ Deployed: {result.release_name}", fg="green")
                )
            elif source and not result.deployed:
                click.echo(click.style("  ✗ Deploy: failed", fg="red"))

            if result.service_started:
                click.echo(click.style("  ✓ Service: running", fg="green"))
            elif not no_start and runtime != "static" and not result.service_started:
                click.echo(click.style("  ✗ Service: failed to start", fg="red"))

            if result.health_status:
                health_color = {
                    "healthy": "green",
                    "degraded": "yellow",
                    "unhealthy": "red",
                }.get(result.health_status, "white")
                click.echo(
                    click.style(f"  Health: {result.health_status}", fg=health_color)
                )

            # Show completed/failed steps
            if result.steps_completed:
                click.echo(f"\n  Completed: {len(result.steps_completed)} step(s)")
            if result.steps_failed:
                click.echo(
                    click.style(
                        f"  Failed: {', '.join(result.steps_failed)}", fg="red"
                    )
                )

            if result.error:
                click.echo(click.style(f"\n  Error: {result.error}", fg="red"))

            click.echo()

            # Next steps hints
            if result.success:
                click.echo("Next steps:")
                if not source:
                    click.echo(
                        f"  • Deploy code: hostkit deploy {name} --source ./your-app"
                    )
                if not actual_domain:
                    click.echo(
                        f"  • Add domain: hostkit nginx add {name} your-domain.com"
                    )
                if actual_domain and not result.ssl_provisioned:
                    click.echo(
                        f"  • Get SSL: hostkit ssl provision {actual_domain}"
                    )
                if result.ssh_keys_added > 0:
                    click.echo(
                        f"  • SSH as project user: ssh {name}@<VPS_IP>"
                    )
                elif not ssh_keys and not github_users:
                    click.echo(
                        f"  • Add SSH access: hostkit ssh add-key {name} --github <username>"
                    )
                click.echo(f"  • Check health: hostkit health {name}")
                click.echo(f"  • View logs: hostkit service logs {name}")

    except ProvisionServiceError as e:
        if formatter and json_mode:
            formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise click.ClickException(f"{e.message}. {e.suggestion or ''}")
    except Exception as e:
        if formatter and json_mode:
            formatter.error(
                code="PROVISION_ERROR",
                message=str(e),
            )
        raise click.ClickException(str(e))

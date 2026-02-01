"""Nginx management CLI commands for HostKit."""

import click

from hostkit.output import OutputFormatter
from hostkit.services.nginx_service import NginxService, NginxError


@click.group()
@click.pass_context
def nginx(ctx: click.Context) -> None:
    """Nginx reverse proxy management.

    Manage Nginx site configurations for projects.
    """
    pass


@nginx.command("list")
@click.pass_context
def nginx_list(ctx: click.Context) -> None:
    """List all Nginx sites.

    Shows all configured Nginx sites with their domains and status.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        sites = service.list_sites()

        if not sites:
            formatter.success(
                data=[],
                message="No Nginx sites configured",
            )
            return

        # Format data for output
        data = []
        for site in sites:
            data.append({
                "project": site.project,
                "domains": ", ".join(site.domains) if site.domains else "(none)",
                "enabled": "yes" if site.enabled else "no",
                "ssl": "yes" if site.ssl_enabled else "no",
                "port": site.port,
            })

        formatter.table(
            data=data,
            columns=[
                ("project", "Project"),
                ("domains", "Domains"),
                ("enabled", "Enabled"),
                ("ssl", "SSL"),
                ("port", "Port"),
            ],
            title="Nginx Sites",
            message="Listed Nginx sites",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("add")
@click.argument("project")
@click.argument("domain")
@click.option("--skip-dns", is_flag=True, help="Skip DNS verification (use when DNS is propagating)")
@click.pass_context
def nginx_add(ctx: click.Context, project: str, domain: str, skip_dns: bool) -> None:
    """Add a domain to a project.

    Configures Nginx to proxy requests for DOMAIN to PROJECT's application.

    Example:
        hostkit nginx add myapp example.com
        hostkit nginx add myapp myapp.192.168.1.1.nip.io
        hostkit nginx add myapp example.com --skip-dns
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        result = service.add_domain(project, domain, skip_dns=skip_dns)
        formatter.success(
            data=result,
            message=f"Domain '{domain}' added to project '{project}'",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("remove")
@click.argument("project")
@click.argument("domain")
@click.pass_context
def nginx_remove(ctx: click.Context, project: str, domain: str) -> None:
    """Remove a domain from a project.

    Removes DOMAIN from PROJECT's Nginx configuration.

    Example:
        hostkit nginx remove myapp example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        result = service.remove_domain(project, domain)
        formatter.success(
            data=result,
            message=f"Domain '{domain}' removed from project '{project}'",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("test")
@click.pass_context
def nginx_test(ctx: click.Context) -> None:
    """Test Nginx configuration.

    Validates the Nginx configuration syntax without reloading.

    Example:
        hostkit nginx test
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        result = service.test_config()
        formatter.success(
            data=result,
            message="Nginx configuration is valid",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("reload")
@click.pass_context
def nginx_reload(ctx: click.Context) -> None:
    """Reload Nginx configuration.

    Tests the configuration first, then gracefully reloads Nginx.

    Example:
        hostkit nginx reload
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        result = service.reload()
        formatter.success(
            data=result,
            message="Nginx configuration reloaded",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("info")
@click.argument("project")
@click.pass_context
def nginx_info(ctx: click.Context, project: str) -> None:
    """Show Nginx site information for a project.

    Displays detailed information about a project's Nginx configuration.

    Example:
        hostkit nginx info myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        site = service.get_site(project)

        data = {
            "project": site.project,
            "domains": site.domains,
            "enabled": site.enabled,
            "ssl_enabled": site.ssl_enabled,
            "port": site.port,
            "config_path": site.config_path,
        }

        formatter.success(
            data=data,
            message=f"Nginx site information for '{project}'",
        )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)


@nginx.command("update-wildcard")
@click.pass_context
def nginx_update_wildcard(ctx: click.Context) -> None:
    """Update the wildcard config auth routes.

    Syncs the auth location regex in the wildcard config (hostkit-wildcard)
    to match the latest AUTH_LOCATION_TEMPLATE. Run this after HostKit updates
    that add new auth endpoints.

    Example:
        hostkit nginx update-wildcard
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = NginxService()

    try:
        result = service.update_wildcard_auth_routes()

        if result.get("updated"):
            added = result.get("added", [])
            if added:
                formatter.success(
                    data=result,
                    message=f"Wildcard config updated. Added routes: {', '.join(added)}",
                )
            else:
                formatter.success(
                    data=result,
                    message="Wildcard config updated with latest auth routes",
                )
        else:
            formatter.success(
                data=result,
                message="Wildcard config already up to date",
            )

    except NginxError as e:
        formatter.error(e.code, e.message, e.suggestion)

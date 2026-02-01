"""DNS management commands for HostKit."""

import socket

import click

from hostkit.output import OutputFormatter
from hostkit.services.dns_service import DNSService, DNSError
from hostkit.config import get_config
from hostkit.services.nginx_service import DEV_DOMAIN_SUFFIXES


@click.group()
@click.pass_context
def dns(ctx: click.Context) -> None:
    """Manage DNS records via Cloudflare and nip.io."""
    pass


@dns.command("config")
@click.option("--token", help="Cloudflare API token")
@click.option("--zone", help="Default zone/domain (e.g., example.com)")
@click.option("--show", is_flag=True, help="Show current configuration")
@click.pass_context
def dns_config(
    ctx: click.Context,
    token: str | None,
    zone: str | None,
    show: bool,
) -> None:
    """Configure Cloudflare DNS credentials.

    Examples:

        # Configure credentials
        hostkit dns config --token YOUR_API_TOKEN --zone example.com

        # Show current configuration
        hostkit dns config --show
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        if show or (not token and not zone):
            # Show current configuration
            info = service.get_config_info()
            formatter.success(info, "DNS configuration")
            return

        if not token:
            formatter.error(
                code="TOKEN_REQUIRED",
                message="API token is required for configuration",
                suggestion="Run 'hostkit dns config --token YOUR_TOKEN --zone example.com'",
            )
            return

        result = service.configure(api_token=token, zone_name=zone)
        formatter.success(result, "DNS configured successfully")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("list")
@click.option("--type", "record_type", help="Filter by record type (A, CNAME, MX, etc.)")
@click.pass_context
def dns_list(ctx: click.Context, record_type: str | None) -> None:
    """List DNS records in the configured zone.

    Examples:

        # List all records
        hostkit dns list

        # List only A records
        hostkit dns list --type A
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        records = service.list_records(record_type=record_type)

        data = [
            {
                "name": rec.name,
                "type": rec.type,
                "content": rec.content,
                "ttl": rec.ttl if rec.ttl != 1 else "auto",
                "proxied": "yes" if rec.proxied else "no",
            }
            for rec in records
        ]

        columns = [
            ("name", "Name"),
            ("type", "Type"),
            ("content", "Content"),
            ("ttl", "TTL"),
            ("proxied", "Proxied"),
        ]

        if formatter.json_mode:
            formatter.success(data, "DNS records retrieved")
        else:
            formatter.table(data, columns, title="DNS Records")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("add")
@click.argument("name")
@click.option("--content", "-c", help="Record content (IP for A records, defaults to VPS IP)")
@click.option("--type", "record_type", default="A", help="Record type (default: A)")
@click.option("--ttl", type=int, default=300, help="TTL in seconds (default: 300)")
@click.option("--proxied", is_flag=True, help="Enable Cloudflare proxy (orange cloud)")
@click.pass_context
def dns_add(
    ctx: click.Context,
    name: str,
    content: str | None,
    record_type: str,
    ttl: int,
    proxied: bool,
) -> None:
    """Add a DNS record pointing to this VPS.

    Examples:

        # Add subdomain pointing to VPS
        hostkit dns add myapp

        # Add with explicit IP
        hostkit dns add myapp --content 1.2.3.4

        # Add with Cloudflare proxy
        hostkit dns add myapp --proxied

        # Add CNAME record
        hostkit dns add www --type CNAME --content myapp.example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        result = service.add_record(
            name=name,
            content=content,
            record_type=record_type,
            ttl=ttl,
            proxied=proxied,
        )
        formatter.success(result, "DNS record created")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("remove")
@click.argument("name")
@click.option("--type", "record_type", default="A", help="Record type (default: A)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def dns_remove(
    ctx: click.Context,
    name: str,
    record_type: str,
    yes: bool,
) -> None:
    """Remove a DNS record.

    Examples:

        # Remove A record
        hostkit dns remove myapp

        # Remove without confirmation
        hostkit dns remove myapp -y

        # Remove CNAME record
        hostkit dns remove www --type CNAME
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        # Get record info first
        record = service.get_record(name, record_type)
        if not record and not formatter.json_mode:
            formatter.error(
                code="RECORD_NOT_FOUND",
                message=f"No {record_type} record found for '{name}'",
            )
            return

        # Confirm deletion
        if not yes and not formatter.json_mode:
            if not click.confirm(
                f"Delete {record_type} record '{record.name}' -> {record.content}?"
            ):
                click.echo("Cancelled")
                return

        result = service.remove_record(name=name, record_type=record_type)
        formatter.success(result, "DNS record removed")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("dev")
@click.argument("project")
@click.pass_context
def dns_dev(ctx: click.Context, project: str) -> None:
    """Get or configure a nip.io development domain for a project.

    nip.io provides instant DNS for any IP. No configuration needed.
    The domain format is: project.ip.nip.io

    Examples:

        # Get dev domain for project
        hostkit dns dev myapp
        # Returns: myapp.<VPS_IP>.nip.io
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        result = service.configure_dev_domain(project)
        formatter.success(result, "Development domain ready")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("ip")
@click.option("--refresh", is_flag=True, help="Force refresh IP from external service")
@click.pass_context
def dns_ip(ctx: click.Context, refresh: bool) -> None:
    """Show the VPS public IP address.

    The IP is cached for 1 hour. Use --refresh to force update.

    Examples:

        # Show cached IP
        hostkit dns ip

        # Force refresh
        hostkit dns ip --refresh
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        ip = service.get_vps_ip(force_refresh=refresh)
        formatter.success(
            {"ip": ip, "refreshed": refresh},
            f"VPS IP: {ip}",
        )

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("setup")
@click.argument("project")
@click.option("--subdomain", "-s", help="Subdomain name (defaults to project name)")
@click.pass_context
def dns_setup(ctx: click.Context, project: str, subdomain: str | None) -> None:
    """Set up a production subdomain for a project (DNS + Nginx).

    This combines DNS record creation and Nginx configuration.

    Examples:

        # Set up myapp.example.com for project myapp
        hostkit dns setup myapp

        # Set up custom subdomain
        hostkit dns setup myapp --subdomain api
        # Creates: api.example.com -> project myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DNSService()

    try:
        result = service.add_subdomain_for_project(
            project_name=project,
            subdomain=subdomain,
        )
        formatter.success(result, "Production domain configured")

    except DNSError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@dns.command("check")
@click.argument("domain")
@click.pass_context
def dns_check(ctx: click.Context, domain: str) -> None:
    """Check if a domain resolves to this VPS IP address.

    Use this before running 'hostkit nginx add' to verify DNS is configured correctly.
    Dev domains (.nip.io, .sslip.io) are automatically validated.

    Examples:

        # Check a production domain
        hostkit dns check myapp.example.com

        # Check a dev domain (always passes)
        hostkit dns check myapp.<VPS_IP>.nip.io

        # Check in JSON mode
        hostkit dns check myapp.example.com --json
    """
    formatter: OutputFormatter = ctx.obj["formatter"]

    # Check if dev domain
    is_dev = any(domain.endswith(suffix) for suffix in DEV_DOMAIN_SUFFIXES)

    if is_dev:
        result = {
            "domain": domain,
            "is_dev_domain": True,
            "ready": True,
            "vps_ip": get_config().vps_ip,
        }

        if formatter.json_mode:
            formatter.success(result, "DNS check passed")
        else:
            click.echo(f"✓ Domain: {domain}")
            click.echo("✓ Dev domain detected (nip.io/sslip.io)")
            click.echo(f"→ Ready for: hostkit nginx add <project> {domain}")
        return

    # Resolve the domain
    resolved_ip = None
    try:
        addr_info = socket.getaddrinfo(domain, None, socket.AF_INET)
        if addr_info:
            resolved_ip = addr_info[0][4][0]
    except socket.gaierror:
        pass

    if resolved_ip is None:
        result = {
            "domain": domain,
            "resolved_ip": None,
            "expected_ip": get_config().vps_ip,
            "ready": False,
            "error": "DNS_RESOLUTION_FAILED",
        }

        if formatter.json_mode:
            formatter.error(
                code="DNS_RESOLUTION_FAILED",
                message=f"Domain '{domain}' could not be resolved",
                suggestion=f"Add an A record pointing to {get_config().vps_ip}",
            )
        else:
            click.echo(f"✗ Domain: {domain}")
            click.echo("✗ Resolves to: (could not resolve)")
            click.echo(f"✗ Expected: {get_config().vps_ip}")
            click.echo(f"→ Add an A record for '{domain}' pointing to {get_config().vps_ip}")
        ctx.exit(1)

    if resolved_ip != get_config().vps_ip:
        result = {
            "domain": domain,
            "resolved_ip": resolved_ip,
            "expected_ip": get_config().vps_ip,
            "ready": False,
            "error": "DNS_MISMATCH",
        }

        if formatter.json_mode:
            formatter.error(
                code="DNS_MISMATCH",
                message=f"Domain '{domain}' resolves to {resolved_ip}, expected {get_config().vps_ip}",
                suggestion=f"Update the domain's A record to point to {get_config().vps_ip}",
            )
        else:
            click.echo(f"✗ Domain: {domain}")
            click.echo(f"✗ Resolves to: {resolved_ip}")
            click.echo(f"✗ Expected: {get_config().vps_ip}")
            click.echo(f"→ Update DNS A record to point to {get_config().vps_ip}")
        ctx.exit(1)

    # Success
    result = {
        "domain": domain,
        "resolved_ip": resolved_ip,
        "expected_ip": get_config().vps_ip,
        "matches": True,
        "ready": True,
    }

    if formatter.json_mode:
        formatter.success(result, "DNS check passed")
    else:
        click.echo(f"✓ Domain: {domain}")
        click.echo(f"✓ Resolves to: {resolved_ip}")
        click.echo("✓ Matches VPS IP: Yes")
        click.echo(f"→ Ready for: hostkit nginx add <project> {domain}")

"""SSL certificate management CLI commands for HostKit."""

import click

from hostkit.output import OutputFormatter
from hostkit.services.ssl_service import SSLService, SSLError


@click.group()
@click.pass_context
def ssl(ctx: click.Context) -> None:
    """SSL certificate management.

    Manage Let's Encrypt SSL certificates for domains.
    """
    pass


@ssl.command("list")
@click.pass_context
def ssl_list(ctx: click.Context) -> None:
    """List all SSL certificates.

    Shows all Let's Encrypt certificates with their expiration dates.
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        certificates = service.list_certificates()

        if not certificates:
            formatter.success(
                data=[],
                message="No SSL certificates found",
            )
            return

        # Format data for output
        data = []
        for cert in certificates:
            # Determine status color hint
            if cert.days_remaining < 0:
                status = "expired"
            elif cert.days_remaining < 7:
                status = "critical"
            elif cert.days_remaining < 30:
                status = "warning"
            else:
                status = "valid"

            data.append({
                "domain": cert.domain,
                "expires": cert.valid_until,
                "days_remaining": cert.days_remaining,
                "status": status,
                "alt_names": len(cert.subject_alt_names),
            })

        formatter.table(
            data=data,
            columns=[
                ("domain", "Domain"),
                ("expires", "Expires"),
                ("days_remaining", "Days Left"),
                ("status", "Status"),
                ("alt_names", "SANs"),
            ],
            title="SSL Certificates",
            message="Listed SSL certificates",
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("status")
@click.argument("domain")
@click.pass_context
def ssl_status(ctx: click.Context, domain: str) -> None:
    """Show certificate status for a domain.

    Displays detailed information about an SSL certificate.

    Example:
        hostkit ssl status example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        status = service.get_certificate_status(domain)
        formatter.success(
            data=status,
            message=f"Certificate status for '{domain}'",
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("provision")
@click.argument("domain")
@click.option("--email", help="Admin email for Let's Encrypt registration")
@click.pass_context
def ssl_provision(ctx: click.Context, domain: str, email: str | None) -> None:
    """Provision SSL certificate for a domain.

    Gets a Let's Encrypt certificate for DOMAIN and configures Nginx to use it.

    The domain must already be configured in Nginx and DNS must point to this server.

    Example:
        hostkit ssl provision example.com
        hostkit ssl provision example.com --email admin@example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        result = service.provision(domain, email)
        formatter.success(
            data=result,
            message=f"SSL certificate provisioned for '{domain}'",
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("renew")
@click.option("--all", "renew_all", is_flag=True, help="Renew all certificates due for renewal")
@click.option("--domain", help="Specific domain to renew")
@click.option("--force", is_flag=True, help="Force renewal even if not due")
@click.pass_context
def ssl_renew(ctx: click.Context, renew_all: bool, domain: str | None, force: bool) -> None:
    """Renew SSL certificates.

    Renews certificates that are due for renewal (or all if --force is used).

    Example:
        hostkit ssl renew --all
        hostkit ssl renew --domain example.com
        hostkit ssl renew --domain example.com --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        result = service.renew(domain=domain, force=force)
        formatter.success(
            data={
                "renewed": result["renewed"],
                "skipped": result["skipped"],
            },
            message=result["message"],
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("auto-renewal")
@click.pass_context
def ssl_auto_renewal(ctx: click.Context) -> None:
    """Check auto-renewal timer status.

    Shows whether the Certbot auto-renewal timer is active.

    Example:
        hostkit ssl auto-renewal
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        status = service.check_auto_renewal()
        formatter.success(
            data=status,
            message=f"Auto-renewal is {status['status']}",
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("enable-auto-renewal")
@click.pass_context
def ssl_enable_auto_renewal(ctx: click.Context) -> None:
    """Enable auto-renewal timer.

    Enables the Certbot systemd timer for automatic certificate renewal.

    Example:
        hostkit ssl enable-auto-renewal
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        result = service.enable_auto_renewal()
        formatter.success(
            data=result,
            message="Auto-renewal timer enabled",
        )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)


@ssl.command("test-renewal")
@click.option("--domain", help="Specific domain to test")
@click.pass_context
def ssl_test_renewal(ctx: click.Context, domain: str | None) -> None:
    """Test certificate renewal (dry run).

    Tests the renewal process without actually renewing certificates.

    Example:
        hostkit ssl test-renewal
        hostkit ssl test-renewal --domain example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SSLService()

    try:
        result = service.test_renewal(domain)

        if result["success"]:
            formatter.success(
                data={"success": True},
                message="Renewal test passed",
            )
        else:
            formatter.error(
                code="TEST_FAILED",
                message="Renewal test failed",
                suggestion="Check the certificate configuration",
            )

    except SSLError as e:
        formatter.error(e.code, e.message, e.suggestion)

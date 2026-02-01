"""Mail server management commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.mail_service import MailError, MailService


@click.group()
@click.pass_context
def mail(ctx: click.Context) -> None:
    """Manage mail server (Postfix + Dovecot).

    Configure email domains, create mailboxes, and manage the mail queue.
    Supports DKIM signing and generates required DNS records.

    Project-scoped commands:
        hostkit mail enable <project>      Enable mail for a project
        hostkit mail disable <project>     Disable mail for a project
        hostkit mail add <project> <local> Add mailbox (e.g., "support")
        hostkit mail remove <project> <local> Remove mailbox
        hostkit mail list <project>        List project mailboxes
        hostkit mail credentials <project> Show SMTP/IMAP credentials
    """
    pass


@mail.command("status")
@click.pass_context
def mail_status(ctx: click.Context) -> None:
    """Show mail server status.

    Displays status of Postfix, Dovecot, and OpenDKIM services,
    along with configured domains and mailbox count.

    Example:
        hostkit mail status
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        services = service.get_service_status()
        domains = service.list_domains()
        mailboxes = service.list_mailboxes()

        data = {
            "installed": service.is_installed(),
            "services": services,
            "domains_count": len(domains),
            "mailboxes_count": len(mailboxes),
            "domains": [d.name for d in domains],
        }

        if ctx.obj["json_mode"]:
            formatter.success(message="Mail server status", data=data)
        else:
            click.echo("\nMail Server Status")
            click.echo("=" * 50)

            # Installation status
            if not data["installed"]:
                click.echo("  Status: NOT INSTALLED")
                click.echo("\n  Run 'hostkit mail setup' to install")
                return

            # Service status
            click.echo("\nServices:")
            click.echo("-" * 30)
            for svc_name, svc_status in services.items():
                status_icon = "●" if svc_status["running"] else "○"
                status_text = "running" if svc_status["running"] else svc_status["status"]
                click.echo(f"  {status_icon} {svc_name}: {status_text}")

            # Domains and mailboxes
            click.echo(f"\nDomains: {len(domains)}")
            click.echo(f"Mailboxes: {len(mailboxes)}")

            if domains:
                click.echo("\nConfigured Domains:")
                click.echo("-" * 30)
                for domain in domains:
                    dkim_status = "✓ DKIM" if domain.dkim_enabled else "○ No DKIM"
                    click.echo(
                        f"  {domain.name} ({len(domain.mailboxes)} mailbox(es)) - {dkim_status}"
                    )

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("setup")
@click.option("--hostname", required=True, help="Mail server hostname (e.g., mail.example.com)")
@click.pass_context
def mail_setup(ctx: click.Context, hostname: str) -> None:
    """Initial mail server setup.

    Installs and configures Postfix, Dovecot, and OpenDKIM.
    Run this once during initial VPS setup.

    Example:
        hostkit mail setup --hostname mail.example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.setup_mail_server(hostname)

        if ctx.obj["json_mode"]:
            formatter.success(message="Mail server setup complete", data=result)
        else:
            click.echo("\nMail Server Setup Complete")
            click.echo("=" * 50)
            click.echo(f"  Hostname: {hostname}")
            click.echo("\nServices Configured:")
            for svc, status in result.get("services", {}).items():
                if svc == "firewall":
                    continue  # Show firewall separately
                status_icon = "✓" if status else "✗"
                click.echo(f"  {status_icon} {svc}")

            # Show firewall configuration
            firewall = result.get("services", {}).get("firewall", {})
            if firewall.get("ports_opened"):
                click.echo("\nFirewall Ports Opened:")
                for port in firewall["ports_opened"]:
                    click.echo(f"  ✓ {port}")
            if firewall.get("errors"):
                click.echo("\nFirewall Warnings:")
                for err in firewall["errors"]:
                    click.echo(f"  ⚠ {err}")

            click.echo("\nNext Steps:")
            click.echo("  1. Add a mail domain: hostkit mail add-domain example.com")
            click.echo("  2. Configure DNS records (shown after adding domain)")
            click.echo("  3. Create mailboxes: hostkit mail add-address user@example.com myproject")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("domains")
@click.pass_context
def mail_domains(ctx: click.Context) -> None:
    """List configured mail domains.

    Shows all domains configured for mail with their DKIM status
    and mailbox count.

    Example:
        hostkit mail domains
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        domains = service.list_domains()

        if ctx.obj["json_mode"]:
            data = [
                {
                    "name": d.name,
                    "dkim_enabled": d.dkim_enabled,
                    "dkim_selector": d.dkim_selector,
                    "mailboxes": d.mailboxes,
                    "mailbox_count": len(d.mailboxes),
                    "created_at": d.created_at,
                }
                for d in domains
            ]
            formatter.success(
                message=f"Found {len(domains)} mail domain(s)", data={"domains": data}
            )
        else:
            if not domains:
                click.echo("\nNo mail domains configured")
                click.echo("Run 'hostkit mail add-domain <domain>' to add one")
                return

            click.echo(f"\nMail Domains ({len(domains)})")
            click.echo("=" * 60)

            for domain in domains:
                dkim_status = f"DKIM: {domain.dkim_selector}" if domain.dkim_enabled else "No DKIM"
                click.echo(f"\n  {domain.name}")
                click.echo(f"    {dkim_status}")
                click.echo(f"    Mailboxes: {len(domain.mailboxes)}")
                if domain.mailboxes:
                    for mb in domain.mailboxes[:5]:
                        click.echo(f"      - {mb}")
                    if len(domain.mailboxes) > 5:
                        click.echo(f"      ... and {len(domain.mailboxes) - 5} more")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("add-domain")
@click.argument("domain")
@click.option("--selector", default="default", help="DKIM selector (default: 'default')")
@click.pass_context
def mail_add_domain(ctx: click.Context, domain: str, selector: str) -> None:
    """Add a mail domain.

    Configures the domain for sending and receiving email,
    generates DKIM keys, and outputs required DNS records.

    Example:
        hostkit mail add-domain example.com
        hostkit mail add-domain example.com --selector mail2024
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.add_domain(domain, selector)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mail domain '{domain}' configured", data=result)
        else:
            click.echo(f"\nMail Domain '{domain}' Configured")
            click.echo("=" * 60)
            click.echo(f"  DKIM Selector: {result['dkim_selector']}")

            click.echo("\nRequired DNS Records:")
            click.echo("-" * 60)
            for name, record in result.get("dns_records", {}).items():
                click.echo(f"\n  [{record['type']}] {record['name']}")
                click.echo(f"    Value: {record['content']}")
                if record.get("description"):
                    click.echo(f"    ({record['description']})")

            click.echo("\nNext Steps:")
            click.echo("  1. Add the DNS records shown above")
            click.echo("  2. Wait for DNS propagation (5-30 minutes)")
            click.echo(f"  3. Verify: hostkit mail dns-check {domain}")
            click.echo(f"  4. Create mailboxes: hostkit mail add-address user@{domain} <project>")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("remove-domain")
@click.argument("domain")
@click.option("--force", is_flag=True, help="Force removal even if mailboxes exist")
@click.pass_context
def mail_remove_domain(ctx: click.Context, domain: str, force: bool) -> None:
    """Remove a mail domain.

    Removes the domain from mail configuration and deletes DKIM keys.
    Use --force to also remove all mailboxes for the domain.

    Example:
        hostkit mail remove-domain example.com --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.remove_domain(domain, force=force)
        formatter.success(message=f"Mail domain '{domain}' removed", data=result)

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("mailboxes")
@click.option("--domain", "-d", help="Filter by domain")
@click.pass_context
def mail_mailboxes(ctx: click.Context, domain: str | None) -> None:
    """List mailboxes.

    Shows all configured mailboxes with their associated projects.

    Example:
        hostkit mail mailboxes
        hostkit mail mailboxes --domain example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        mailboxes = service.list_mailboxes(domain=domain)

        if ctx.obj["json_mode"]:
            data = [
                {
                    "address": m.address,
                    "domain": m.domain,
                    "project": m.project,
                    "maildir": m.maildir,
                    "created_at": m.created_at,
                }
                for m in mailboxes
            ]
            formatter.success(
                message=f"Found {len(mailboxes)} mailbox(es)", data={"mailboxes": data}
            )
        else:
            filter_text = f" for '{domain}'" if domain else ""
            if not mailboxes:
                click.echo(f"\nNo mailboxes configured{filter_text}")
                click.echo("Run 'hostkit mail add-address <email> <project>' to create one")
                return

            click.echo(f"\nMailboxes{filter_text} ({len(mailboxes)})")
            click.echo("=" * 60)

            for mb in mailboxes:
                click.echo(f"\n  {mb.address}")
                click.echo(f"    Project: {mb.project}")
                click.echo(f"    Maildir: {mb.maildir}")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("add-address")
@click.argument("address")
@click.argument("project")
@click.option("--password", "-p", help="Set password (auto-generated if not provided)")
@click.pass_context
def mail_add_address(ctx: click.Context, address: str, project: str, password: str | None) -> None:
    """Create a mailbox for a project.

    Creates a virtual mailbox associated with a project.
    Generates IMAP credentials for receiving and SMTP for sending.

    Example:
        hostkit mail add-address info@example.com myapp
        hostkit mail add-address support@example.com myapp --password secretpass
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.add_mailbox(address, project, password=password)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mailbox '{address}' created", data=result)
        else:
            click.echo(f"\nMailbox '{address}' Created")
            click.echo("=" * 60)
            click.echo(f"  Project: {project}")
            click.echo(f"  Maildir: {result['maildir']}")

            click.echo("\nCredentials:")
            click.echo("-" * 40)
            click.echo(f"  Username: {address}")
            click.echo(f"  Password: {result['password']}")

            click.echo("\nServer Settings:")
            click.echo("-" * 40)
            click.echo(f"  IMAP Server: {result['imap_server']}")
            click.echo(f"  IMAP Port: {result['imap_port']} (SSL)")
            click.echo(f"  SMTP Server: {result['smtp_server']}")
            click.echo(f"  SMTP Port: {result['smtp_port']} (STARTTLS)")

            click.echo("\n  Save these credentials - the password won't be shown again!")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("remove-address")
@click.argument("address")
@click.pass_context
def mail_remove_address(ctx: click.Context, address: str) -> None:
    """Remove a mailbox.

    Removes the mailbox from configuration but preserves the maildir.

    Example:
        hostkit mail remove-address info@example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.remove_mailbox(address)
        formatter.success(message=f"Mailbox '{address}' removed", data=result)

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("queue")
@click.pass_context
def mail_queue(ctx: click.Context) -> None:
    """Show mail queue.

    Displays queued messages waiting for delivery with their status,
    sender, and recipients.

    Example:
        hostkit mail queue
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.get_queue()

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mail queue: {result['count']} message(s)", data=result)
        else:
            if result["count"] == 0:
                click.echo("\nMail queue is empty")
                return

            click.echo(f"\nMail Queue ({result['count']} message(s))")
            click.echo("=" * 70)

            for entry in result["entries"]:
                status_icon = "●" if entry["status"] == "active" else "○"
                click.echo(f"\n  {status_icon} {entry['queue_id']} ({entry['size']} bytes)")
                click.echo(f"    Time: {entry['arrival_time']}")
                click.echo(f"    From: {entry['sender']}")
                click.echo(f"    To: {', '.join(entry['recipients'])}")
                click.echo(f"    Status: {entry['status']}")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("flush")
@click.option("--id", "queue_id", help="Specific queue ID to flush (all if not specified)")
@click.pass_context
def mail_flush(ctx: click.Context, queue_id: str | None) -> None:
    """Flush mail queue.

    Forces immediate delivery attempt for queued messages.

    Example:
        hostkit mail flush
        hostkit mail flush --id ABC123DEF456
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.flush_queue(queue_id=queue_id)
        formatter.success(message="Mail queue flush initiated", data=result)

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("purge")
@click.option("--force", is_flag=True, required=True, help="Confirm purging all messages")
@click.pass_context
def mail_purge(ctx: click.Context, force: bool) -> None:
    """Purge all queued messages.

    Deletes all messages from the mail queue. Use with caution.

    Example:
        hostkit mail purge --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.purge_queue()
        formatter.success(message="Mail queue purged", data=result)

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("dns-records")
@click.argument("domain")
@click.pass_context
def mail_dns_records(ctx: click.Context, domain: str) -> None:
    """Show required DNS records for a domain.

    Displays all DNS records needed for mail delivery (MX, SPF, DKIM, DMARC).

    Example:
        hostkit mail dns-records example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        records = service.generate_dns_records(domain)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"DNS records for '{domain}'", data={"records": records})
        else:
            click.echo(f"\nDNS Records for '{domain}'")
            click.echo("=" * 70)

            for name, record in records.items():
                click.echo(f"\n  [{record['type']}] {record['name']}")
                click.echo(f"    Value: {record['content']}")
                if record.get("description"):
                    click.echo(f"    ({record['description']})")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("dns-check")
@click.argument("domain")
@click.pass_context
def mail_dns_check(ctx: click.Context, domain: str) -> None:
    """Check DNS records for a mail domain.

    Verifies that required DNS records are configured correctly.

    Example:
        hostkit mail dns-check example.com
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.get_domain_dns_status(domain)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"DNS check for '{domain}'", data=result)
        else:
            all_ok = result["all_ok"]
            status_text = "All records OK" if all_ok else "Some records missing"

            click.echo(f"\nDNS Check for '{domain}' - {status_text}")
            click.echo("=" * 70)

            for name, record in result["records"].items():
                status_icon = "✓" if record["ok"] else "✗"
                click.echo(f"\n  {status_icon} [{record['type']}] {record['name']}")
                click.echo(f"    Expected: {record['expected']}")
                click.echo(f"    Found: {record['found'] or '(not found)'}")

            if not all_ok:
                click.echo("\n  Some DNS records are missing or incorrect.")
                click.echo(f"  Run 'hostkit mail dns-records {domain}' to see required records.")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Project-Scoped Mail Commands
# ─────────────────────────────────────────────────────────────────────────────


@mail.command("enable")
@click.argument("project")
@click.pass_context
@project_owner()
def mail_enable(ctx: click.Context, project: str) -> None:
    """Enable mail for a project.

    Creates a subdomain (project.hostkit.dev) and a default noreply mailbox.
    The project can then send and receive email.

    Example:
        hostkit mail enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.enable_project_mail(project)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mail enabled for '{project}'", data=result)
        else:
            click.echo(f"\nMail Enabled for '{project}'")
            click.echo("=" * 60)
            click.echo(f"  Domain: {result['domain']}")
            click.echo(f"  Default Address: {result['default_address']}")

            click.echo("\nCredentials:")
            click.echo("-" * 40)
            click.echo(f"  Username: {result['default_address']}")
            click.echo(f"  Password: {result['password']}")

            click.echo("\nServer Settings:")
            click.echo("-" * 40)
            click.echo("  SMTP Server: mail.hostkit.dev")
            click.echo("  SMTP Port: 587 (STARTTLS)")
            click.echo("  IMAP Server: mail.hostkit.dev")
            click.echo("  IMAP Port: 993 (SSL)")

            click.echo("\nFor sending from your app:")
            click.echo("-" * 40)
            click.echo("  SMTP_HOST=mail.hostkit.dev")
            click.echo("  SMTP_PORT=587")
            click.echo(f"  SMTP_USER={result['default_address']}")
            click.echo(f"  SMTP_PASS={result['password']}")
            click.echo(f"  SMTP_FROM={result['default_address']}")

            click.echo("\n  Save these credentials - the password won't be shown again!")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Force disable (removes all mailboxes)")
@click.pass_context
@project_owner()
def mail_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable mail for a project.

    Removes the project's mail domain and all associated mailboxes.
    Use --force to confirm removal.

    Example:
        hostkit mail disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.disable_project_mail(project, force=force)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mail disabled for '{project}'", data=result)
        else:
            click.echo(f"\nMail Disabled for '{project}'")
            click.echo("=" * 60)
            click.echo(f"  Domain removed: {result['domain']}")
            click.echo(f"  Mailboxes removed: {result['mailboxes_removed']}")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("add")
@click.argument("project")
@click.argument("local_part")
@click.option("--password", "-p", help="Set password (auto-generated if not provided)")
@click.pass_context
@project_owner()
def mail_add(ctx: click.Context, project: str, local_part: str, password: str | None) -> None:
    """Add a mailbox for a project.

    Creates a mailbox using the project's subdomain.
    The local_part is the part before @ (e.g., "support" creates support@project.hostkit.dev).

    Example:
        hostkit mail add myapp support
        hostkit mail add myapp info --password secretpass
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.add_project_mailbox(project, local_part, password=password)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Mailbox '{result['address']}' created", data=result)
        else:
            click.echo(f"\nMailbox '{result['address']}' Created")
            click.echo("=" * 60)
            click.echo(f"  Project: {project}")

            click.echo("\nCredentials:")
            click.echo("-" * 40)
            click.echo(f"  Username: {result['address']}")
            click.echo(f"  Password: {result['password']}")

            click.echo("\nServer Settings:")
            click.echo("-" * 40)
            click.echo("  IMAP Server: mail.hostkit.dev")
            click.echo("  IMAP Port: 993 (SSL)")
            click.echo("  SMTP Server: mail.hostkit.dev")
            click.echo("  SMTP Port: 587 (STARTTLS)")

            click.echo("\n  Save these credentials - the password won't be shown again!")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("remove")
@click.argument("project")
@click.argument("local_part")
@click.pass_context
@project_owner()
def mail_remove(ctx: click.Context, project: str, local_part: str) -> None:
    """Remove a mailbox from a project.

    Removes the mailbox from configuration but preserves the maildir.

    Example:
        hostkit mail remove myapp support
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.remove_project_mailbox(project, local_part)

        if ctx.obj["json_mode"]:
            formatter.success(message="Mailbox removed", data=result)
        else:
            click.echo(f"\nMailbox '{result['address']}' Removed")
            click.echo("=" * 60)
            click.echo(f"  Maildir preserved at: {result['maildir']}")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("list")
@click.argument("project")
@click.pass_context
@project_owner()
def mail_list(ctx: click.Context, project: str) -> None:
    """List mailboxes for a project.

    Shows all configured mailboxes for the project.

    Example:
        hostkit mail list myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.list_project_mailboxes(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Found {len(result['mailboxes'])} mailbox(es)",
                data=result,
            )
        else:
            if not result["enabled"]:
                click.echo(f"\nMail not enabled for '{project}'")
                click.echo("Run 'hostkit mail enable {project}' to enable")
                return

            click.echo(f"\nMailboxes for '{project}'")
            click.echo("=" * 60)
            click.echo(f"  Domain: {result['domain']}")
            click.echo(f"  Mailboxes: {len(result['mailboxes'])}")

            if result["mailboxes"]:
                click.echo("\n  Addresses:")
                click.echo("  " + "-" * 40)
                for mb in result["mailboxes"]:
                    click.echo(f"    {mb['address']}")
            else:
                click.echo("\n  No mailboxes configured")
                click.echo(f"  Run 'hostkit mail add {project} <local>' to create one")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("credentials")
@click.argument("project")
@click.argument("local_part", required=False)
@click.option("--reset-password", is_flag=True, help="Generate a new password")
@click.pass_context
@project_owner()
def mail_credentials(
    ctx: click.Context, project: str, local_part: str | None, reset_password: bool
) -> None:
    """Show or reset credentials for a project mailbox.

    If local_part is not specified, shows credentials for the default noreply address.

    Example:
        hostkit mail credentials myapp
        hostkit mail credentials myapp support
        hostkit mail credentials myapp --reset-password
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.get_project_credentials(project, local_part, reset_password=reset_password)

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Credentials for '{result['address']}'", data=result)
        else:
            action = "Reset" if reset_password else "Retrieved"
            click.echo(f"\nCredentials {action} for '{result['address']}'")
            click.echo("=" * 60)

            click.echo("\nCredentials:")
            click.echo("-" * 40)
            click.echo(f"  Username: {result['address']}")
            if result.get("password"):
                click.echo(f"  Password: {result['password']}")
                click.echo("\n  Save this password - it won't be shown again!")
            else:
                click.echo("  Password: (unchanged - use --reset-password to generate new)")

            click.echo("\nServer Settings:")
            click.echo("-" * 40)
            click.echo("  SMTP Server: mail.hostkit.dev")
            click.echo("  SMTP Port: 587 (STARTTLS)")
            click.echo("  IMAP Server: mail.hostkit.dev")
            click.echo("  IMAP Port: 993 (SSL)")

            click.echo("\nEnvironment Variables:")
            click.echo("-" * 40)
            click.echo("  SMTP_HOST=mail.hostkit.dev")
            click.echo("  SMTP_PORT=587")
            click.echo(f"  SMTP_USER={result['address']}")
            if result.get("password"):
                click.echo(f"  SMTP_PASS={result['password']}")
            click.echo(f"  SMTP_FROM={result['address']}")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@mail.command("send-test")
@click.argument("project")
@click.argument("to_email")
@click.option(
    "--from", "from_local", default="noreply", help="Local part to send from (default: noreply)"
)
@click.pass_context
@project_owner()
def mail_send_test(ctx: click.Context, project: str, to_email: str, from_local: str) -> None:
    """Send a test email from a project mailbox.

    Useful for verifying mail configuration is working.

    Example:
        hostkit mail send-test myapp user@example.com
        hostkit mail send-test myapp user@example.com --from support
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = MailService()

    try:
        result = service.send_test_email(project, to_email, from_local)

        if ctx.obj["json_mode"]:
            formatter.success(message="Test email sent", data=result)
        else:
            click.echo("\nTest Email Sent")
            click.echo("=" * 60)
            click.echo(f"  From: {result['from']}")
            click.echo(f"  To: {result['to']}")
            click.echo(f"  Subject: {result['subject']}")
            click.echo("\n  Check the recipient's inbox (and spam folder)")

    except MailError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

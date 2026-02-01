"""Alert notification CLI commands for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.alert_service import (
    AlertService,
    AlertServiceError,
    WebhookConfig,
    EmailConfig,
    SlackConfig,
)


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


def parse_duration(duration_str: str) -> int:
    """Parse a duration string like '1h', '30m', '2d' into minutes."""
    duration_str = duration_str.lower().strip()

    if duration_str.endswith("d"):
        return int(duration_str[:-1]) * 24 * 60
    elif duration_str.endswith("h"):
        return int(duration_str[:-1]) * 60
    elif duration_str.endswith("m"):
        return int(duration_str[:-1])
    else:
        # Assume minutes if no suffix
        return int(duration_str)


@click.group()
def alert() -> None:
    """Alert notification management.

    Configure webhook, email, and Slack notifications for deployment,
    migration, and health check events.

    \b
    Channel Types:
      webhook  - HTTP POST to a URL (optional HMAC signing)
      email    - Email notifications via HostKit mail
      slack    - Slack messages via Incoming Webhooks

    \b
    Supported Events:
      deploy    - Deployment success/failure
      migrate   - Migration success/failure
      health    - Health check failures
      test      - Test notifications

    \b
    Usage:
      hostkit alert channel add myapp webhook --url https://...
      hostkit alert channel add myapp email --to ops@example.com
      hostkit alert channel add myapp slack --webhook-url https://hooks.slack.com/...
      hostkit alert mute myapp --duration 1h
      hostkit alert history myapp
    """
    pass


@alert.group("channel")
def channel() -> None:
    """Manage alert notification channels."""
    pass


@channel.command("add")
@click.argument("project")
@click.argument("channel_type", type=click.Choice(["webhook", "email", "slack"]))
@click.option("--name", default="default", help="Channel name (default: 'default')")
# Webhook options
@click.option("--url", default=None, help="Webhook URL (for webhook type)")
@click.option("--secret", default=None, help="HMAC secret for webhook signing")
# Email options
@click.option("--to", "to_emails", multiple=True, help="Email recipient(s) (for email type)")
@click.option("--from", "from_address", default=None, help="From address (for email type)")
@click.option("--subject-prefix", default=None, help="Subject prefix (for email type)")
# Slack options
@click.option("--webhook-url", default=None, help="Slack webhook URL (for slack type)")
@click.pass_context
@project_owner("project")
def add_channel(
    ctx: click.Context,
    project: str,
    channel_type: str,
    name: str,
    url: str | None,
    secret: str | None,
    to_emails: tuple[str, ...],
    from_address: str | None,
    subject_prefix: str | None,
    webhook_url: str | None,
) -> None:
    """Add a notification channel.

    Supports three channel types: webhook, email, and slack.

    \b
    Webhook:
      Sends JSON POST requests with optional HMAC signing.
      hostkit alert channel add myapp webhook --url https://hooks.example.com

    \b
    Email:
      Sends plain text emails via HostKit mail.
      hostkit alert channel add myapp email --to ops@example.com
      hostkit alert channel add myapp email --to ops@example.com --to dev@example.com

    \b
    Slack:
      Sends rich Slack messages via Incoming Webhooks.
      hostkit alert channel add myapp slack --webhook-url https://hooks.slack.com/...

    \b
    Examples:
      hostkit alert channel add myapp webhook --url https://... --secret mysecret
      hostkit alert channel add myapp email --to ops@example.com --name ops-email
      hostkit alert channel add myapp slack --webhook-url https://hooks.slack.com/...
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()
        channel = service.add_channel(
            project_name=project,
            name=name,
            channel_type=channel_type,
            url=url,
            secret=secret,
            to_emails=list(to_emails) if to_emails else None,
            from_address=from_address,
            subject_prefix=subject_prefix,
            webhook_url=webhook_url,
        )

        # Build output based on channel type
        config = channel.config
        if formatter.json_mode:
            data = {
                "id": channel.id,
                "project": channel.project_name,
                "name": channel.name,
                "type": channel.channel_type,
                "enabled": channel.enabled,
                "created_at": channel.created_at,
            }
            if isinstance(config, WebhookConfig):
                data["url"] = config.url
                data["signed"] = config.secret is not None
            elif isinstance(config, EmailConfig):
                data["to"] = config.to
                data["from"] = config.from_address
            elif isinstance(config, SlackConfig):
                data["webhook_url"] = config.webhook_url
            formatter.success(data=data, message="Channel added successfully")
        else:
            click.echo(click.style("\nChannel added successfully\n", fg="green", bold=True))
            click.echo(f"  Name:     {channel.name}")
            click.echo(f"  Type:     {channel.channel_type}")
            if isinstance(config, WebhookConfig):
                click.echo(f"  URL:      {config.url}")
                click.echo(f"  Signed:   {'Yes' if config.secret else 'No'}")
            elif isinstance(config, EmailConfig):
                click.echo(f"  To:       {', '.join(config.to)}")
                click.echo(f"  From:     {config.from_address}")
            elif isinstance(config, SlackConfig):
                click.echo(f"  Webhook:  {config.webhook_url[:40]}...")
            click.echo(f"  Enabled:  Yes")
            click.echo("")
            click.echo("Test with: hostkit alert channel test " + project + " " + name)

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@channel.command("list")
@click.argument("project")
@click.pass_context
def list_channels(ctx: click.Context, project: str) -> None:
    """List notification channels for a project.

    \b
    Examples:
      hostkit alert channel list myapp
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()
        channels = service.list_channels(project_name=project)

        if formatter.json_mode:
            channel_data = []
            for ch in channels:
                config = ch.config
                item = {
                    "id": ch.id,
                    "name": ch.name,
                    "type": ch.channel_type,
                    "enabled": ch.enabled,
                    "muted_until": ch.muted_until,
                    "is_muted": ch.is_muted,
                    "created_at": ch.created_at,
                }
                if isinstance(config, WebhookConfig):
                    item["url"] = config.url
                    item["signed"] = config.secret is not None
                elif isinstance(config, EmailConfig):
                    item["to"] = config.to
                    item["from"] = config.from_address
                elif isinstance(config, SlackConfig):
                    item["webhook_url"] = config.webhook_url
                channel_data.append(item)

            formatter.success(
                data={
                    "channels": channel_data,
                    "count": len(channels),
                    "project": project,
                },
                message=f"Found {len(channels)} channel(s)",
            )
        else:
            if not channels:
                click.echo(f"No alert channels configured for {project}.")
                click.echo("\nAdd one with: hostkit alert channel add " + project + " webhook --url <url>")
                return

            click.echo(f"\nAlert channels for {project} ({len(channels)} total):\n")

            # Header
            click.echo(f"{'Name':<15} {'Type':<8} {'Target':<35} {'Enabled':<8} {'Muted'}")
            click.echo("-" * 80)

            for ch in channels:
                config = ch.config
                # Get target based on channel type
                if isinstance(config, WebhookConfig):
                    target = config.url
                elif isinstance(config, EmailConfig):
                    target = ", ".join(config.to[:2])
                    if len(config.to) > 2:
                        target += f" (+{len(config.to) - 2})"
                elif isinstance(config, SlackConfig):
                    target = "Slack webhook"
                else:
                    target = "-"

                if len(target) > 32:
                    target = target[:29] + "..."

                enabled = "Yes" if ch.enabled else "No"
                muted = "Yes" if ch.is_muted else "No"

                click.echo(f"{ch.name:<15} {ch.channel_type:<8} {target:<35} {enabled:<8} {muted}")

            click.echo("")

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@channel.command("remove")
@click.argument("project")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def remove_channel(ctx: click.Context, project: str, name: str, force: bool) -> None:
    """Remove a notification channel.

    \b
    Examples:
      hostkit alert channel remove myapp default --force
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()

        # Verify channel exists
        channel = service.get_channel(project, name)

        if not force and not formatter.json_mode:
            click.echo(f"\nAbout to remove channel '{name}' for project '{project}'")
            click.echo(f"  Type: {channel.channel_type}")
            if not click.confirm("\nContinue?"):
                click.echo("Cancelled.")
                return

        result = service.remove_channel(project, name)

        if formatter.json_mode:
            formatter.success(data=result, message="Channel removed")
        else:
            click.echo(click.style(f"Channel '{name}' removed", fg="green"))

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@channel.command("test")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner("project")
def test_channel(ctx: click.Context, project: str, name: str) -> None:
    """Send a test notification to a channel.

    \b
    Examples:
      hostkit alert channel test myapp default
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()

        if not formatter.json_mode:
            click.echo(f"Sending test notification to '{name}'...")

        result = service.test_channel(project, name)

        if formatter.json_mode:
            formatter.success(data=result, message="Test complete")
        else:
            if result["success"]:
                click.echo(click.style("\nTest notification sent successfully", fg="green", bold=True))
            else:
                click.echo(click.style(f"\nTest notification failed: {result['error']}", fg="red", bold=True))

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@channel.command("enable")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner("project")
def enable_channel(ctx: click.Context, project: str, name: str) -> None:
    """Enable a notification channel.

    \b
    Examples:
      hostkit alert channel enable myapp default
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()
        channel = service.enable_channel(project, name)

        if formatter.json_mode:
            formatter.success(
                data={"project": project, "channel": name, "enabled": True},
                message="Channel enabled",
            )
        else:
            click.echo(click.style(f"Channel '{name}' enabled", fg="green"))

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@channel.command("disable")
@click.argument("project")
@click.argument("name")
@click.pass_context
@project_owner("project")
def disable_channel(ctx: click.Context, project: str, name: str) -> None:
    """Disable a notification channel.

    Disabled channels will not receive notifications until re-enabled.

    \b
    Examples:
      hostkit alert channel disable myapp default
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()
        channel = service.disable_channel(project, name)

        if formatter.json_mode:
            formatter.success(
                data={"project": project, "channel": name, "enabled": False},
                message="Channel disabled",
            )
        else:
            click.echo(click.style(f"Channel '{name}' disabled", fg="yellow"))

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@alert.command("history")
@click.argument("project")
@click.option("--limit", "-n", default=20, help="Maximum entries to show (default: 20)")
@click.option("--type", "event_type", default=None, help="Filter by event type")
@click.pass_context
def alert_history(ctx: click.Context, project: str, limit: int, event_type: str | None) -> None:
    """View alert history for a project.

    Shows recent alerts sent to notification channels.

    \b
    Examples:
      hostkit alert history myapp
      hostkit alert history myapp --limit 50
      hostkit alert history myapp --type deploy
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()
        history = service.get_history(
            project_name=project,
            event_type=event_type,
            limit=limit,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "history": history,
                    "count": len(history),
                    "project": project,
                },
                message=f"Found {len(history)} alert(s)",
            )
        else:
            if not history:
                click.echo(f"No alert history for {project}.")
                return

            click.echo(f"\nAlert history for {project} ({len(history)} entries):\n")

            # Header
            click.echo(f"{'ID':<6} {'Event':<12} {'Status':<10} {'Channel':<15} {'Sent':<6} {'Time'}")
            click.echo("-" * 80)

            for h in history:
                event = h["event_type"]
                status = h["event_status"]
                channel = h["channel"] or "-"
                sent = "Yes" if h["sent"] else "No"
                created = h["created_at"][:19].replace("T", " ")

                # Color-code status
                if status == "success":
                    status_style = click.style(status, fg="green")
                else:
                    status_style = click.style(status, fg="red")

                # Color-code sent
                if h["sent"]:
                    sent_style = click.style(sent, fg="green")
                else:
                    sent_style = click.style(sent, fg="red")

                click.echo(f"{h['id']:<6} {event:<12} {status_style:<19} {channel:<15} {sent_style:<15} {created}")

                if h["error"]:
                    click.echo(f"       Error: {h['error']}")

            click.echo("")

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@alert.command("mute")
@click.argument("project")
@click.option("--duration", "-d", default="1h", help="Mute duration (e.g., 30m, 1h, 2d). Default: 1h")
@click.option("--channel", "-c", default=None, help="Mute specific channel only")
@click.pass_context
@project_owner("project")
def mute_alerts(ctx: click.Context, project: str, duration: str, channel: str | None) -> None:
    """Temporarily mute alerts for a project.

    Muted channels will not send notifications until the mute expires
    or is manually removed with 'hostkit alert unmute'.

    \b
    Duration formats:
      30m  - 30 minutes
      1h   - 1 hour
      2d   - 2 days
      120  - 120 minutes

    \b
    Examples:
      hostkit alert mute myapp                    # Mute all channels for 1 hour
      hostkit alert mute myapp --duration 2h     # Mute for 2 hours
      hostkit alert mute myapp -c default -d 30m # Mute specific channel
    """
    formatter = get_formatter(ctx)

    try:
        duration_minutes = parse_duration(duration)
    except ValueError:
        formatter.error(
            code="INVALID_DURATION",
            message=f"Invalid duration format: {duration}",
            suggestion="Use format: 30m, 1h, 2d, or just a number for minutes",
        )
        raise SystemExit(1)

    try:
        service = AlertService()

        if channel:
            # Mute specific channel
            ch = service.mute_channel(project, channel, duration_minutes)
            channels = [ch]
        else:
            # Mute all channels
            channels = service.mute_project(project, duration_minutes)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "muted_channels": [
                        {"name": ch.name, "muted_until": ch.muted_until}
                        for ch in channels
                    ],
                    "duration_minutes": duration_minutes,
                },
                message=f"Muted {len(channels)} channel(s)",
            )
        else:
            if not channels:
                click.echo(f"No channels to mute for {project}.")
                return

            click.echo(click.style(f"\nMuted {len(channels)} channel(s) for {duration}", fg="yellow", bold=True))
            for ch in channels:
                click.echo(f"  - {ch.name} (until {ch.muted_until})")
            click.echo("")
            click.echo("Unmute with: hostkit alert unmute " + project)

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@alert.command("unmute")
@click.argument("project")
@click.option("--channel", "-c", default=None, help="Unmute specific channel only")
@click.pass_context
@project_owner("project")
def unmute_alerts(ctx: click.Context, project: str, channel: str | None) -> None:
    """Remove mute from alerts for a project.

    \b
    Examples:
      hostkit alert unmute myapp             # Unmute all channels
      hostkit alert unmute myapp -c default  # Unmute specific channel
    """
    formatter = get_formatter(ctx)

    try:
        service = AlertService()

        if channel:
            # Unmute specific channel
            ch = service.unmute_channel(project, channel)
            channels = [ch]
        else:
            # Unmute all channels
            channels = service.unmute_project(project)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "unmuted_channels": [ch.name for ch in channels],
                },
                message=f"Unmuted {len(channels)} channel(s)",
            )
        else:
            if not channels:
                click.echo(f"No channels to unmute for {project}.")
                return

            click.echo(click.style(f"\nUnmuted {len(channels)} channel(s)", fg="green", bold=True))
            for ch in channels:
                click.echo(f"  - {ch.name}")
            click.echo("")

    except AlertServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

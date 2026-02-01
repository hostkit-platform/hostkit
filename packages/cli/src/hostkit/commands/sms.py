"""SMS service management commands for HostKit."""

import click
import json

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.sms_service import SMSService, SMSServiceError


@click.group()
@click.pass_context
def sms(ctx: click.Context) -> None:
    """Manage per-project SMS services.

    Enable transactional and conversational SMS messaging via Twilio,
    with consent tracking, templates, and AI integration.
    """
    pass


@sms.command("enable")
@click.argument("project")
@click.option("--phone-number", help="Use specific phone number (default: share with voice)")
@click.option("--ai", is_flag=True, help="Enable conversational AI")
@click.option("--agent", help="Default AI agent name")
@click.pass_context
@project_owner("project")
def sms_enable(ctx: click.Context, project: str, phone_number: str | None, ai: bool, agent: str | None) -> None:
    """Enable SMS service for a project.

    Creates SMS database tables, configures Twilio webhooks,
    and sets up default message templates.

    Example:
        hostkit sms enable myapp
        hostkit sms enable myapp --ai --agent booking_assistant
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    try:
        result = service.enable_sms(
            project=project,
            phone_number=phone_number,
            ai_enabled=ai,
            default_agent=agent,
        )

        formatter.success(
            message=f"SMS service enabled for '{project}'",
            data={
                "project": project,
                "phone_number": result["phone_number"],
                "sms_port": result["sms_port"],
                "sms_db": result["sms_db"],
                "ai_enabled": ai,
                "default_agent": agent,
                "webhook_url": result["webhook_url"],
                "templates_created": result["templates_created"],
            },
        )

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@sms.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def sms_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable SMS service for a project.

    Removes SMS database tables and configuration.
    Requires --force to confirm.

    WARNING: This will delete all SMS data!

    Example:
        hostkit sms disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    try:
        service.disable_sms(project=project, force=force)

        formatter.success(
            message=f"SMS service disabled for '{project}'",
            data={
                "project": project,
                "sms_tables_deleted": True,
            },
        )

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@sms.command("status")
@click.argument("project")
@click.pass_context
@project_access("project")
def sms_status(ctx: click.Context, project: str) -> None:
    """Show SMS service status for a project.

    Displays configuration, phone number, and message statistics.

    Example:
        hostkit sms status myapp
        hostkit --json sms status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    try:
        status = service.get_sms_status(project=project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"SMS status for '{project}'",
                data=status,
            )
        else:
            click.echo(f"\nSMS Status: {project}")
            click.echo("-" * 60)

            if not status["enabled"]:
                click.echo("  Status: DISABLED")
                click.echo(f"\n  Enable with: hostkit sms enable {project}")
            else:
                click.echo(f"  Status: ENABLED")
                click.echo(f"  Phone Number: {status['phone_number']}")
                click.echo(f"  SMS Port: {status['sms_port']}")
                click.echo(f"  AI Enabled: {status['ai_enabled']}")
                if status['default_agent']:
                    click.echo(f"  Default Agent: {status['default_agent']}")
                click.echo(f"  Messages Today: {status['messages_today']}")
                click.echo(f"  Active Conversations: {status['active_conversations']}")
                click.echo(f"  Webhook URL: {status['webhook_url']}")

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@sms.command("send")
@click.argument("project")
@click.option("--to", required=True, help="Recipient phone number (E.164 format)")
@click.option("--template", help="Template name")
@click.option("--body", help="Message body (if no template)")
@click.option("--vars", help="Template variables (JSON)")
@click.option("--skip-consent", is_flag=True, help="Skip consent check (OTP only)")
@click.pass_context
@project_access("project")
def sms_send(
    ctx: click.Context,
    project: str,
    to: str,
    template: str | None,
    body: str | None,
    vars: str | None,
    skip_consent: bool,
) -> None:
    """Send an SMS message.

    Either --template or --body is required.
    Consent checking is enforced unless --skip-consent (OTP only).

    Example:
        hostkit sms send myapp --to +15551234567 --template booking_confirmation --vars '{"name":"John"}'
        hostkit sms send myapp --to +15551234567 --body "Your code is 123456" --skip-consent
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    if not template and not body:
        formatter.error(
            code="MISSING_CONTENT",
            message="Either --template or --body is required",
            suggestion="Provide --template name or --body text",
        )
        raise SystemExit(1)

    variables = {}
    if vars:
        try:
            variables = json.loads(vars)
        except json.JSONDecodeError:
            formatter.error(
                code="INVALID_JSON",
                message="Invalid JSON in --vars parameter",
                suggestion="Ensure --vars is valid JSON",
            )
            raise SystemExit(1)

    try:
        result = service.send_sms(
            project=project,
            to=to,
            template=template,
            body=body,
            variables=variables,
            skip_consent_check=skip_consent,
        )

        formatter.success(
            message="SMS sent successfully",
            data={
                "message_id": result["message_id"],
                "to": to,
                "status": result["status"],
                "segments": result["segments"],
            },
        )

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@sms.command("template")
@click.argument("action", type=click.Choice(["list", "create", "show", "update", "delete"]))
@click.argument("project")
@click.argument("name", required=False)
@click.option("--body", help="Template body text")
@click.option("--category", type=click.Choice(["transactional", "marketing", "otp"]), default="transactional")
@click.option("--no-opt-out", is_flag=True, help="Don't append opt-out text")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_access("project")
def sms_template(
    ctx: click.Context,
    action: str,
    project: str,
    name: str | None,
    body: str | None,
    category: str,
    no_opt_out: bool,
    force: bool,
) -> None:
    """Manage SMS templates.

    Actions: list, create, show, update, delete

    Examples:
        hostkit sms template list myapp
        hostkit sms template create myapp welcome --body "Welcome {{name}}!" --category transactional
        hostkit sms template show myapp welcome
        hostkit sms template delete myapp welcome --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    try:
        if action == "list":
            templates = service.list_templates(project)
            if ctx.obj["json_mode"]:
                formatter.success(message=f"Templates for {project}", data={"templates": templates})
            else:
                click.echo(f"\nSMS Templates: {project}")
                click.echo("-" * 60)
                for tmpl in templates:
                    click.echo(f"  {tmpl['name']} ({tmpl['category']}) - Sent {tmpl['times_sent']} times")

        elif action == "create":
            if not name or not body:
                formatter.error(
                    code="MISSING_ARGUMENTS",
                    message="Template name and --body are required",
                    suggestion="Provide both name and --body arguments",
                )
                raise SystemExit(1)

            result = service.create_template(
                project=project,
                name=name,
                body=body,
                category=category,
                include_opt_out=not no_opt_out,
            )
            formatter.success(message=f"Template '{name}' created", data=result)

        elif action == "show":
            if not name:
                formatter.error(code="MISSING_NAME", message="Template name required", suggestion="Provide template name")
                raise SystemExit(1)

            template = service.get_template(project, name)
            if ctx.obj["json_mode"]:
                formatter.success(message=f"Template '{name}'", data=template)
            else:
                click.echo(f"\nTemplate: {name}")
                click.echo("-" * 60)
                click.echo(f"  Category: {template['category']}")
                click.echo(f"  Include Opt-out: {template['include_opt_out']}")
                click.echo(f"  Times Sent: {template['times_sent']}")
                click.echo(f"\n  Body:\n  {template['body']}")

        elif action == "update":
            if not name:
                formatter.error(code="MISSING_NAME", message="Template name required", suggestion="Provide template name")
                raise SystemExit(1)

            result = service.update_template(project=project, name=name, body=body, category=category)
            formatter.success(message=f"Template '{name}' updated", data=result)

        elif action == "delete":
            if not name:
                formatter.error(code="MISSING_NAME", message="Template name required", suggestion="Provide template name")
                raise SystemExit(1)
            if not force:
                formatter.error(
                    code="FORCE_REQUIRED",
                    message="The --force flag is required to delete templates",
                    suggestion=f"Add --force to confirm: 'hostkit sms template delete {project} {name} --force'",
                )
                raise SystemExit(1)

            service.delete_template(project, name)
            formatter.success(message=f"Template '{name}' deleted", data={"deleted": True})

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@sms.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def sms_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View SMS service logs for a project.

    Shows logs from the project's SMS service.
    Use --follow to stream logs in real-time.

    Example:
        hostkit sms logs myapp
        hostkit sms logs myapp --lines 50
        hostkit sms logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = SMSService()

    try:
        # Verify SMS is enabled for project
        if not service.sms_is_enabled(project):
            raise SMSServiceError(
                code="SMS_NOT_ENABLED",
                message=f"SMS service is not enabled for '{project}'",
                suggestion=f"Enable SMS first with 'hostkit sms enable {project}'",
            )

        service_name = f"hostkit-{project}-sms"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following SMS logs for {project}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            proc = subprocess.Popen(
                ["journalctl", "-u", f"{service_name}.service", "-f", "-n", str(lines)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            try:
                for line in iter(proc.stdout.readline, b""):
                    sys.stdout.write(line.decode())
                    sys.stdout.flush()
            except KeyboardInterrupt:
                proc.terminate()
                click.echo("\n--- Log stream ended ---")
        else:
            result = subprocess.run(
                ["journalctl", "-u", f"{service_name}.service", "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
            )

            logs = result.stdout

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"SMS logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nSMS logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except SMSServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

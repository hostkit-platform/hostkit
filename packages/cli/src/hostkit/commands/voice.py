"""Voice service management commands for HostKit."""

import json

import click

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.voice_service import VoiceService, VoiceServiceError


@click.group()
@click.pass_context
def voice(ctx: click.Context) -> None:
    """Manage voice calling service (central service for all projects).

    HostKit Voice enables AI-powered phone calls via Twilio Media Streams.
    Real-time streaming with Deepgram STT, Cartesia TTS, and OpenAI LLM.
    """
    pass


@voice.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def voice_enable(ctx: click.Context, project: str) -> None:
    """Enable voice service for a project.

    Creates project voice configuration directory and agent config.
    The central voice service (port 8900) handles all projects.

    Example:
        hostkit voice enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    try:
        result = service.enable_voice(project=project)

        formatter.success(
            message=f"Voice service enabled for '{project}'",
            data={
                "project": project,
                "voice_url": result["voice_url"],
                "webhook_url": result["webhook_url"],
                "api_key": result["api_key"],
                "config_dir": result["config_dir"],
            },
        )

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@voice.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def voice_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable voice service for a project.

    Removes voice configuration and cancels active calls.
    Requires --force to confirm.

    WARNING: This will cancel any active calls for this project!

    Example:
        hostkit voice disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    try:
        service.disable_voice(project=project, force=force)

        formatter.success(
            message=f"Voice service disabled for '{project}'",
            data={
                "project": project,
                "config_deleted": True,
            },
        )

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@voice.command("status")
@click.argument("project")
@click.pass_context
@project_access("project")
def voice_status(ctx: click.Context, project: str) -> None:
    """Show voice service status for a project.

    Displays configuration, phone numbers, and call statistics.

    Example:
        hostkit voice status myapp
        hostkit --json voice status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    try:
        status = service.get_voice_status(project=project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Voice status for '{project}'",
                data=status,
            )
        else:
            click.echo(f"\nVoice Status: {project}")
            click.echo("-" * 60)

            if not status["enabled"]:
                click.echo("  Status: DISABLED")
                click.echo(f"\n  Enable with: hostkit voice enable {project}")
            else:
                click.echo("  Status: ENABLED")
                click.echo(f"  Voice URL: {status['voice_url']}")
                click.echo(f"  Default Agent: {status['default_agent']}")
                click.echo(f"  Calls Today: {status['calls_today']}")
                click.echo(f"  Active Calls: {status['active_calls']}")

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@voice.command("agent")
@click.argument("action", type=click.Choice(["create", "list"]))
@click.argument("project")
@click.argument("name", required=False)
@click.pass_context
@project_access("project")
def voice_agent(ctx: click.Context, action: str, project: str, name: str | None) -> None:
    """Manage voice agents.

    Actions: create, list

    Examples:
        hostkit voice agent list myapp
        hostkit voice agent create myapp booking_assistant
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    try:
        if action == "list":
            agents = service.list_agents(project)
            if ctx.obj["json_mode"]:
                formatter.success(message=f"Agents for {project}", data={"agents": agents})
            else:
                click.echo(f"\nVoice Agents: {project}")
                click.echo("-" * 60)
                if not agents:
                    click.echo("  No agents configured")
                else:
                    for agent in agents:
                        click.echo(f"  {agent['name']} - {agent['description']}")

        elif action == "create":
            if not name:
                formatter.error(
                    code="MISSING_NAME",
                    message="Agent name required",
                    suggestion="Provide agent name argument",
                )
                raise SystemExit(1)

            result = service.create_agent(project=project, name=name)
            formatter.success(message=f"Agent '{name}' created", data=result)

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@voice.command("call")
@click.argument("action", type=click.Choice(["initiate"]))
@click.argument("project")
@click.argument("agent")
@click.option("--to", required=True, help="Recipient phone number (E.164 format)")
@click.option("--context", help="Call context (JSON)")
@click.pass_context
@project_access("project")
def voice_call(
    ctx: click.Context,
    action: str,
    project: str,
    agent: str,
    to: str,
    context: str | None,
) -> None:
    """Manage voice calls.

    Actions: initiate

    Example:
        hostkit voice call initiate myapp booking_assistant --to +15551234567
        hostkit voice call initiate myapp booking_assistant \\
            --to +15551234567 --context '{"customer_id":"123"}'
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    call_context = {}
    if context:
        try:
            call_context = json.loads(context)
        except json.JSONDecodeError:
            formatter.error(
                code="INVALID_JSON",
                message="Invalid JSON in --context parameter",
                suggestion="Ensure --context is valid JSON",
            )
            raise SystemExit(1)

    try:
        if action == "initiate":
            result = service.initiate_call(
                project=project,
                agent=agent,
                to=to,
                context=call_context,
            )

            formatter.success(
                message="Call initiated successfully",
                data={
                    "call_id": result["call_id"],
                    "status": result["status"],
                    "to": to,
                    "agent": agent,
                },
            )

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@voice.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def voice_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View voice service logs for a project.

    Shows logs from the central voice service filtered by project.
    Use --follow to stream logs in real-time.

    Example:
        hostkit voice logs myapp
        hostkit voice logs myapp --lines 50
        hostkit voice logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VoiceService()

    try:
        # Verify voice is enabled for project
        if not service.voice_is_enabled(project):
            raise VoiceServiceError(
                code="VOICE_NOT_ENABLED",
                message=f"Voice service is not enabled for '{project}'",
                suggestion=f"Enable voice first with 'hostkit voice enable {project}'",
            )

        service_name = "hostkit-voice"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following voice logs for {project}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            # Filter by project in logs
            proc = subprocess.Popen(
                ["journalctl", "-u", f"{service_name}.service", "-f", "-n", str(lines)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            try:
                for line in iter(proc.stdout.readline, b""):
                    decoded_line = line.decode()
                    if project in decoded_line:
                        sys.stdout.write(decoded_line)
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
                    message=f"Voice logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nVoice logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                # Filter logs by project
                for line in logs.split("\n"):
                    if project in line:
                        click.echo(line)

    except VoiceServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

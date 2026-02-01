"""Chatbot service management commands for HostKit."""

import click
import json

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.chatbot_service import ChatbotService, ChatbotServiceError


@click.group()
@click.pass_context
def chatbot(ctx: click.Context) -> None:
    """Manage per-project AI chatbot services.

    Enable AI-powered chatbots with embeddable widgets,
    conversation history, and SSE streaming responses.
    """
    pass


@chatbot.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def chatbot_enable(ctx: click.Context, project: str) -> None:
    """Enable chatbot service for a project.

    Creates chatbot database, deploys FastAPI service,
    and configures the embeddable widget.

    Example:
        hostkit chatbot enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    try:
        result = service.enable_chatbot(project=project)

        formatter.success(
            message=f"Chatbot service enabled for '{project}'",
            data={
                "project": project,
                "chatbot_url": result["chatbot_url"],
                "chatbot_port": result["chatbot_port"],
                "chatbot_db": result["chatbot_db"],
                "api_key": result["api_key"],
                "widget_script": result["widget_script"],
            },
        )

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@chatbot.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def chatbot_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable chatbot service for a project.

    Removes chatbot database, service, and configuration.
    Requires --force to confirm.

    WARNING: This will delete all conversation history!

    Example:
        hostkit chatbot disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    try:
        service.disable_chatbot(project=project, force=force)

        formatter.success(
            message=f"Chatbot service disabled for '{project}'",
            data={
                "project": project,
                "chatbot_deleted": True,
            },
        )

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@chatbot.command("status")
@click.argument("project")
@click.option("--show-key", is_flag=True, help="Show the API key")
@click.pass_context
@project_access("project")
def chatbot_status(ctx: click.Context, project: str, show_key: bool) -> None:
    """Show chatbot service status for a project.

    Displays configuration, widget URL, and usage statistics.
    Use --show-key to include the API key in the output.

    Example:
        hostkit chatbot status myapp
        hostkit chatbot status myapp --show-key
        hostkit --json chatbot status myapp --show-key
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    try:
        status = service.get_chatbot_status(project=project, show_key=show_key)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Chatbot status for '{project}'",
                data=status,
            )
        else:
            click.echo(f"\nChatbot Status: {project}")
            click.echo("-" * 60)

            if not status["enabled"]:
                click.echo("  Status: DISABLED")
                click.echo(f"\n  Enable with: hostkit chatbot enable {project}")
            else:
                click.echo("  Status: ENABLED")
                click.echo(f"  Chatbot URL: {status['chatbot_url']}")
                click.echo(f"  Port: {status['chatbot_port']}")
                click.echo(f"  Widget Script: {status['widget_script']}")
                if show_key and status.get('api_key'):
                    click.echo(f"  API Key: {status['api_key']}")
                elif not show_key:
                    click.echo("  API Key: ******** (use --show-key to reveal)")
                click.echo(f"\n  Appearance:")
                click.echo(f"    Name: {status['name']}")
                click.echo(f"    Position: {status['position']}")
                click.echo(f"    Theme: {status['theme']}")
                click.echo(f"    Primary Color: {status['primary_color']}")
                click.echo(f"\n  AI Settings:")
                click.echo(f"    Model: {status['model']}")
                if status.get('system_prompt'):
                    prompt_preview = status['system_prompt'][:50] + "..." if len(status.get('system_prompt', '')) > 50 else status['system_prompt']
                    click.echo(f"    System Prompt: {prompt_preview}")
                if status.get('cta_enabled'):
                    click.echo(f"\n  CTA:")
                    click.echo(f"    Text: {status.get('cta_text', 'N/A')}")
                    click.echo(f"    URL: {status.get('cta_url', 'N/A')}")
                    click.echo(f"    After Messages: {status.get('cta_after_messages', 3)}")
                click.echo(f"\n  Statistics:")
                click.echo(f"    Total Conversations: {status['conversations_total']}")
                click.echo(f"    Total Messages: {status['messages_total']}")

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@chatbot.command("config")
@click.argument("project")
@click.option("--name", help="Chatbot display name")
@click.option("--system-prompt", help="System prompt for the AI")
@click.option("--suggested-questions", help="Suggested questions (JSON array)")
@click.option("--position", type=click.Choice(["bottom-right", "bottom-left", "top-right", "top-left"]),
              help="Widget position")
@click.option("--primary-color", help="Primary color (hex, e.g., #6366f1)")
@click.option("--theme", type=click.Choice(["light", "dark"]), help="Widget theme")
@click.option("--cta-text", help="Call-to-action button text")
@click.option("--cta-url", help="Call-to-action button URL")
@click.option("--cta-after", type=int, help="Show CTA after N messages")
@click.option("--model", help="LLM model to use")
@click.pass_context
@project_owner("project")
def chatbot_config(
    ctx: click.Context,
    project: str,
    name: str | None,
    system_prompt: str | None,
    suggested_questions: str | None,
    position: str | None,
    primary_color: str | None,
    theme: str | None,
    cta_text: str | None,
    cta_url: str | None,
    cta_after: int | None,
    model: str | None,
) -> None:
    """Configure chatbot settings for a project.

    Update widget appearance, behavior, and AI settings.

    Example:
        hostkit chatbot config myapp --name "Support Bot" --theme dark
        hostkit chatbot config myapp --system-prompt "You are a helpful assistant..."
        hostkit chatbot config myapp --suggested-questions '["How does this work?", "Pricing?"]'
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    # Parse suggested questions if provided
    parsed_questions = None
    if suggested_questions:
        try:
            parsed_questions = json.loads(suggested_questions)
            if not isinstance(parsed_questions, list):
                formatter.error(
                    code="INVALID_JSON",
                    message="--suggested-questions must be a JSON array",
                    suggestion='Example: --suggested-questions \'["Question 1", "Question 2"]\'',
                )
                raise SystemExit(1)
        except json.JSONDecodeError:
            formatter.error(
                code="INVALID_JSON",
                message="Invalid JSON in --suggested-questions",
                suggestion="Ensure the value is a valid JSON array",
            )
            raise SystemExit(1)

    try:
        result = service.update_config(
            project=project,
            name=name,
            system_prompt=system_prompt,
            suggested_questions=parsed_questions,
            position=position,
            primary_color=primary_color,
            theme=theme,
            cta_text=cta_text,
            cta_url=cta_url,
            cta_after=cta_after,
            model=model,
        )

        formatter.success(
            message=f"Chatbot configuration updated for '{project}'",
            data=result,
        )

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@chatbot.command("stats")
@click.argument("project")
@click.pass_context
@project_access("project")
def chatbot_stats(ctx: click.Context, project: str) -> None:
    """Show chatbot usage statistics for a project.

    Displays conversation and message counts.

    Example:
        hostkit chatbot stats myapp
        hostkit --json chatbot stats myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    try:
        stats = service.get_stats(project=project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Chatbot stats for '{project}'",
                data=stats,
            )
        else:
            click.echo(f"\nChatbot Statistics: {project}")
            click.echo("-" * 60)
            click.echo(f"  Conversations:")
            click.echo(f"    Total: {stats['conversations_total']}")
            click.echo(f"    Today: {stats['conversations_today']}")
            click.echo(f"  Messages:")
            click.echo(f"    Total: {stats['messages_total']}")
            click.echo(f"    Today: {stats['messages_today']}")
            click.echo(f"    Avg per Conversation: {stats['avg_messages_per_conversation']:.1f}")
            click.echo(f"  CTA Performance:")
            click.echo(f"    Shown: {stats['cta_shown']}")
            click.echo(f"    Clicked: {stats['cta_clicked']}")
            click.echo(f"    Click Rate: {stats['cta_click_rate']:.1f}%")

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@chatbot.command("logs")
@click.argument("project")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100)")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def chatbot_logs(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """View chatbot service logs for a project.

    Shows logs from the project's chatbot service.
    Use --follow to stream logs in real-time.

    Example:
        hostkit chatbot logs myapp
        hostkit chatbot logs myapp --lines 50
        hostkit chatbot logs myapp --follow
    """
    import subprocess
    import sys

    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ChatbotService()

    try:
        # Verify chatbot is enabled for project
        if not service.chatbot_is_enabled(project):
            raise ChatbotServiceError(
                code="CHATBOT_NOT_ENABLED",
                message=f"Chatbot service is not enabled for '{project}'",
                suggestion=f"Enable chatbot first with 'hostkit chatbot enable {project}'",
            )

        service_name = f"hostkit-{project}-chatbot"

        if follow:
            if ctx.obj["json_mode"]:
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Cannot use --follow with JSON output",
                    suggestion="Remove --json flag for streaming logs",
                )
                raise SystemExit(1)

            click.echo(f"Following chatbot logs for {project}... (Ctrl+C to stop)")
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
                    message=f"Chatbot logs for {project}",
                    data={"logs": logs, "lines": lines},
                )
            else:
                click.echo(f"\nChatbot logs for {project} (last {lines} lines):")
                click.echo("-" * 60)
                click.echo(logs)

    except ChatbotServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

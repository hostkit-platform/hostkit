"""Claude daemon CLI commands for HostKit.

Provides AI capabilities for projects via the shared Claude daemon.
"""

import click
import os
from typing import Optional

from hostkit.access import project_owner, root_only
from hostkit.output import OutputFormatter
from hostkit.services.claude_service import ClaudeService, ClaudeServiceError


@click.group()
@click.pass_context
def claude(ctx: click.Context) -> None:
    """Claude AI integration service.

    Enable AI capabilities for projects using the VPS owner's
    Claude/Anthropic subscription.
    """
    pass


# =============================================================================
# Setup (root only)
# =============================================================================


@claude.command("setup")
@click.option(
    "--api-key",
    required=True,
    envvar="ANTHROPIC_API_KEY",
    help="Anthropic API key (or set ANTHROPIC_API_KEY env var)",
)
@click.option("--force", is_flag=True, help="Overwrite existing setup")
@click.pass_context
@root_only
def claude_setup(ctx: click.Context, api_key: str, force: bool) -> None:
    """Initialize the Claude daemon (root only).

    Sets up the shared Claude daemon that serves all projects.
    Requires an Anthropic API key.

    Example:
        hostkit claude setup --api-key sk-ant-xxx
        ANTHROPIC_API_KEY=sk-ant-xxx hostkit claude setup
        hostkit claude setup --api-key sk-ant-xxx --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    try:
        result = service.setup(api_key=api_key, force=force)

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Claude daemon initialized",
                data=result,
            )
        else:
            click.echo()
            click.secho("Claude daemon initialized", fg="green", bold=True)
            click.echo()
            click.echo(f"  Endpoint:    {result['service_url']}")
            click.echo(f"  Database:    {result['database']}")
            click.echo(f"  Service Dir: {result['service_dir']}")
            click.echo(f"  Config File: {result['config_file']}")
            click.echo()
            click.echo("  Next steps:")
            click.echo("    hostkit claude enable <project>  # Enable for a project")
            click.echo("    hostkit claude grant <project> --tools logs,health,db:read")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@claude.command("status")
@click.pass_context
def claude_status(ctx: click.Context) -> None:
    """Show Claude daemon status.

    Displays service health, database connection, and enabled projects.

    Example:
        hostkit claude status
        hostkit --json claude status
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    try:
        result = service.status()

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Claude daemon status",
                data=result,
            )
        else:
            click.echo()
            click.echo("Claude Daemon Status")
            click.echo("-" * 50)

            status = result.get("status", "unknown")
            status_color = "green" if status == "active" else "red"
            click.echo(f"  Service:  ", nl=False)
            click.secho(status, fg=status_color)

            db_status = result.get("database", "unknown")
            db_color = "green" if db_status == "connected" else "red"
            click.echo(f"  Database: ", nl=False)
            click.secho(db_status, fg=db_color)

            if result.get("endpoint"):
                click.echo(f"  Endpoint: {result['endpoint']}")

            click.echo(f"  Projects: {result.get('project_count', 0)} enabled")

            if status != "active":
                click.echo()
                click.secho(
                    "  Service not running. Check: systemctl status hostkit-claude",
                    fg="yellow",
                )

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Project Management
# =============================================================================


@claude.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def claude_enable(ctx: click.Context, project: str) -> None:
    """Enable Claude for a project.

    Generates an API key for the project to access Claude.
    The API key is shown once and should be saved securely.

    Example:
        hostkit claude enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    try:
        result = service.enable_project(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Claude enabled for '{project}'",
                data={
                    "project": result.project_name,
                    "api_key": result.api_key,
                    "api_key_prefix": result.api_key_prefix,
                    "endpoint": result.endpoint,
                },
            )
        else:
            click.echo()
            click.secho(f"Claude enabled for '{project}'", fg="green", bold=True)
            click.echo()
            click.echo(f"  API Key:  {result.api_key}")
            click.echo(f"  Endpoint: {result.endpoint}")
            click.echo()
            click.secho("  Save this API key - it will not be shown again!", fg="yellow")
            click.echo()
            click.echo("  Add to your project's .env:")
            click.echo(f"    CLAUDE_API_KEY={result.api_key}")
            click.echo(f"    CLAUDE_ENDPOINT={result.endpoint}")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@claude.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def claude_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable Claude for a project.

    Revokes the API key and removes Claude access.
    Existing conversations are preserved but inaccessible.

    Example:
        hostkit claude disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    if not force:
        click.confirm(
            f"This will revoke Claude access for '{project}'. Continue?",
            abort=True,
        )

    try:
        result = service.disable_project(project)

        formatter.success(
            message=f"Claude disabled for '{project}'",
            data=result,
        )

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Tool Permissions
# =============================================================================


@claude.command("grant")
@click.argument("project")
@click.option(
    "--tools",
    "-t",
    required=True,
    help="Comma-separated list of tools to grant",
)
@click.pass_context
@project_owner("project")
def claude_grant(ctx: click.Context, project: str, tools: str) -> None:
    """Grant tool permissions to a project.

    Tools allow Claude to perform operations like reading logs,
    checking health, or accessing the database.

    Available tools:
      Tier 1 (read-only):  logs, health, db:read, env:read, vector:search
      Tier 2 (state change): db:write, env:write, service, cache:flush
      Tier 3 (high-risk):  deploy, rollback, migrate

    Example:
        hostkit claude grant myapp --tools logs,health,db:read
        hostkit claude grant myapp -t db:write,service
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    if not tool_list:
        formatter.error(code="INVALID_TOOLS", message="No tools specified")
        raise SystemExit(1)

    # Get current user for audit
    granted_by = os.environ.get("USER", "root")

    try:
        result = service.grant_tools(project, tool_list, granted_by)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Tools granted to '{project}'",
                data=result,
            )
        else:
            click.echo()
            click.secho(f"Tools granted to '{project}'", fg="green")
            for tool in result["granted"]:
                click.echo(f"  - {tool}")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@claude.command("revoke")
@click.argument("project")
@click.option(
    "--tools",
    "-t",
    required=True,
    help="Comma-separated list of tools to revoke",
)
@click.pass_context
@project_owner("project")
def claude_revoke(ctx: click.Context, project: str, tools: str) -> None:
    """Revoke tool permissions from a project.

    Example:
        hostkit claude revoke myapp --tools db:write,deploy
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    if not tool_list:
        formatter.error(code="INVALID_TOOLS", message="No tools specified")
        raise SystemExit(1)

    try:
        result = service.revoke_tools(project, tool_list)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Tools revoked from '{project}'",
                data=result,
            )
        else:
            click.echo()
            click.secho(f"Tools revoked from '{project}'", fg="green")
            for tool in result["revoked"]:
                click.echo(f"  - {tool}")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@claude.command("tools")
@click.argument("project")
@click.pass_context
@project_owner("project")
def claude_tools(ctx: click.Context, project: str) -> None:
    """List granted tools for a project.

    Example:
        hostkit claude tools myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    try:
        result = service.list_tools(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Tools for '{project}'",
                data=result,
            )
        else:
            tools = result.get("tools", [])
            if not tools:
                click.echo(f"\nNo tools granted to '{project}'")
                click.echo("\nGrant tools with:")
                click.echo(f"  hostkit claude grant {project} --tools logs,health,db:read")
            else:
                click.echo(f"\nTools granted to '{project}'")
                click.echo("-" * 40)
                for tool in tools:
                    click.echo(f"  - {tool}")
                click.echo("-" * 40)
                click.echo(f"Total: {len(tools)} tool(s)")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Usage Statistics
# =============================================================================


@claude.command("usage")
@click.argument("project", required=False)
@click.option("--detailed", is_flag=True, help="Show per-conversation breakdown")
@click.option("--all-projects", is_flag=True, help="Show all projects (root only)")
@click.pass_context
def claude_usage(
    ctx: click.Context,
    project: Optional[str],
    detailed: bool,
    all_projects: bool,
) -> None:
    """Show Claude usage statistics.

    Without arguments, shows usage for all enabled projects.
    With a project name, shows detailed usage for that project.

    Example:
        hostkit claude usage myapp
        hostkit claude usage myapp --detailed
        hostkit claude usage --all-projects
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = ClaudeService()

    if not project and not all_projects:
        formatter.error(
            code="MISSING_ARGUMENT",
            message="Specify a project name or use --all-projects",
        )
        raise SystemExit(1)

    try:
        if project:
            result = service.get_usage(project, detailed=detailed)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Usage for '{project}'",
                    data=result,
                )
            else:
                today = result.get("today", {})
                month = result.get("this_month", {})
                limits = result.get("limits", {})

                click.echo(f"\nClaude Usage: {project}")
                click.echo("-" * 50)
                click.echo()
                click.echo("  Today:")
                click.echo(f"    Requests:      {today.get('requests', 0):,}")
                click.echo(f"    Input tokens:  {today.get('input_tokens', 0):,}")
                click.echo(f"    Output tokens: {today.get('output_tokens', 0):,}")
                click.echo(f"    Tool calls:    {today.get('tool_calls', 0):,}")
                click.echo()
                click.echo("  This Month:")
                click.echo(f"    Requests:      {month.get('requests', 0):,}")
                click.echo(f"    Input tokens:  {month.get('input_tokens', 0):,}")
                click.echo(f"    Output tokens: {month.get('output_tokens', 0):,}")
                click.echo(f"    Tool calls:    {month.get('tool_calls', 0):,}")
                click.echo()
                click.echo("  Limits:")
                click.echo(f"    Rate limit:       {limits.get('rate_limit_rpm', 60)} req/min")
                click.echo(f"    Daily token limit: {limits.get('daily_token_limit', 1000000):,}")
                click.echo(f"    Remaining today:   {limits.get('remaining_tokens', 0):,}")

        else:
            # All projects - to be implemented
            click.echo("\nAll project usage not yet implemented")

    except ClaudeServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

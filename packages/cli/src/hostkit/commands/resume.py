"""Resume command for HostKit."""

import click

from hostkit.access import project_owner
from hostkit.output import OutputFormatter
from hostkit.services.auto_pause_service import AutoPauseService, AutoPauseError


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


@click.command()
@click.argument("project")
@click.option("--force", is_flag=True, help="Also reset failure history to prevent immediate re-pause")
@click.pass_context
@project_owner("project")
def resume(ctx: click.Context, project: str, force: bool) -> None:
    """Resume a paused project.

    When a project is auto-paused due to repeated failures, use this command
    to resume deployments.

    \b
    Options:
      --force    Also clears the failure history, preventing immediate
                 re-pause if the failure threshold would still be exceeded.

    \b
    Examples:
      hostkit resume myapp
      hostkit resume myapp --force  # Reset failure count too
    """
    formatter = get_formatter(ctx)

    try:
        service = AutoPauseService()

        # Check if actually paused
        if not service.is_paused(project):
            if formatter.json_mode:
                formatter.success(
                    data={
                        "project": project,
                        "was_paused": False,
                        "message": "Project is not paused",
                    },
                    message="Project is not paused",
                )
            else:
                click.echo(f"\nProject '{project}' is not paused.")
                click.echo()
            return

        result = service.resume(project, reset_failures=force)

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "resumed": result["resumed"],
                    "resumed_at": result["resumed_at"],
                    "failures_reset": result["failures_reset"],
                },
                message="Project resumed",
            )
        else:
            click.echo()
            click.echo(click.style(f"Project '{project}' resumed!", fg="green", bold=True))
            click.echo(f"  Resumed at: {result['resumed_at']}")

            if result["failures_reset"]:
                click.echo(f"  {click.style('Failure history cleared', fg='yellow')}")
            else:
                click.echo()
                click.echo(
                    click.style(
                        "Note: If failures persist, the project may be paused again. "
                        "Use --force to also reset failure history.",
                        fg="yellow",
                    )
                )
            click.echo()

    except AutoPauseError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

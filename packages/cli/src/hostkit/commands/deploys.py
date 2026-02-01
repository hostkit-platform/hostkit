"""Deploy history CLI command for HostKit."""

import click

from hostkit.output import OutputFormatter
from hostkit.services.rate_limit_service import RateLimitError, RateLimitService


def get_formatter(ctx: click.Context) -> OutputFormatter:
    """Get the output formatter from context."""
    return ctx.obj["formatter"]


@click.command("deploys")
@click.argument("project")
@click.option("--since", "-s", default=None, help="Show deploys since (e.g., 1h, 24h, 7d)")
@click.option("--limit", "-n", default=20, help="Maximum entries (default: 20)")
@click.pass_context
def deploys(ctx: click.Context, project: str, since: str | None, limit: int) -> None:
    """View deployment history for a project.

    Shows recent deploy attempts with their status, duration, and any errors.
    Useful for understanding deploy patterns and diagnosing issues.

    \b
    Duration formats:
      1h   - Last hour
      24h  - Last 24 hours
      7d   - Last 7 days
      30m  - Last 30 minutes

    \b
    Examples:
      hostkit deploys myapp
      hostkit deploys myapp --since 1h
      hostkit deploys myapp --since 24h --limit 50
      hostkit deploys myapp --json
    """
    formatter = get_formatter(ctx)

    try:
        service = RateLimitService()
        history = service.get_deploy_history(
            project_name=project,
            since=since,
            limit=limit,
        )

        if formatter.json_mode:
            formatter.success(
                data={
                    "project": project,
                    "deploys": [
                        {
                            "id": d["id"],
                            "deployed_at": d["deployed_at"],
                            "deployed_by": d["deployed_by"],
                            "success": bool(d["success"]),
                            "duration_ms": d["duration_ms"],
                            "source_type": d["source_type"],
                            "files_synced": d["files_synced"],
                            "override_used": bool(d["override_used"]),
                            "error_message": d["error_message"],
                        }
                        for d in history
                    ],
                    "count": len(history),
                    "since": since,
                },
                message=f"Found {len(history)} deploy(s)",
            )
        else:
            if not history:
                since_msg = f" since {since}" if since else ""
                click.echo(f"No deployments found for {project}{since_msg}.")
                return

            title = f"Deploy history for {project}"
            if since:
                title += f" (since {since})"

            click.echo(f"\n{title} ({len(history)} total):\n")

            # Header
            header = (
                f"{'ID':<5} {'Time':<20} {'Status':<8} "
                f"{'Duration':<10} {'Files':<7} {'By':<12} "
                f"{'Override'}"
            )
            click.echo(header)
            click.echo("-" * 85)

            for d in history:
                # Format time
                time_str = d["deployed_at"][:19].replace("T", " ")

                # Format status
                if d["success"]:
                    status = click.style("OK", fg="green")
                else:
                    status = click.style("FAIL", fg="red")

                # Format duration
                if d["duration_ms"]:
                    secs = d["duration_ms"] / 1000
                    if secs >= 60:
                        duration = f"{secs / 60:.1f}m"
                    else:
                        duration = f"{secs:.1f}s"
                else:
                    duration = "-"

                # Format files
                files = str(d["files_synced"]) if d["files_synced"] else "-"

                # Format override
                override = "Yes" if d["override_used"] else "-"

                # Truncate deployed_by
                deployed_by = d["deployed_by"][:12] if d["deployed_by"] else "-"

                row = (
                    f"{d['id']:<5} {time_str:<20} "
                    f"{status:<17} {duration:<10} "
                    f"{files:<7} {deployed_by:<12} "
                    f"{override}"
                )
                click.echo(row)

                # Show error if failed
                if not d["success"] and d["error_message"]:
                    error_msg = d["error_message"][:70]
                    click.echo(f"      {click.style('Error:', fg='red')} {error_msg}")

            click.echo()

            # Summary
            success_count = sum(1 for d in history if d["success"])
            fail_count = len(history) - success_count

            if fail_count > 0:
                ok = click.style(str(success_count), fg="green")
                fail = click.style(str(fail_count), fg="red")
                click.echo(f"Summary: {ok} succeeded, {fail} failed")
            else:
                click.echo(f"Summary: {click.style(str(success_count), fg='green')} succeeded")
            click.echo()

    except ValueError as e:
        formatter.error(
            code="INVALID_DURATION",
            message=str(e),
            suggestion="Use format: 1h, 24h, 7d, 30m",
        )
        raise SystemExit(1)
    except RateLimitError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

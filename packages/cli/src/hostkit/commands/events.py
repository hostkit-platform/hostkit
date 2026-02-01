"""Events command for HostKit CLI."""

import click

from hostkit.access import project_access
from hostkit.output import OutputFormatter
from hostkit.services.event_service import EventService, EventServiceError


@click.group(name="events")
@click.pass_context
def events(ctx: click.Context) -> None:
    """View structured HostKit operation events.

    Events are structured records of HostKit operations like deploys,
    health checks, migrations, and more. Use this to understand what
    happened on a project.
    """
    ctx.ensure_object(dict)


@events.command(name="list")
@click.argument("project")
@click.option(
    "-c",
    "--category",
    help="Filter by category (deploy, health, auth, migrate, etc.). Comma-separate for multiple.",
)
@click.option(
    "-l",
    "--level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    help="Minimum log level",
)
@click.option("--since", help="Show events since time (e.g., '1h', '24h', '7d', '2025-12-15')")
@click.option("--until", help="Show events until time")
@click.option("-n", "--limit", default=50, help="Maximum events to show")
@click.option("--offset", default=0, help="Skip first N events")
@click.pass_context
@project_access("project")
def list_events(
    ctx: click.Context,
    project: str,
    category: str | None,
    level: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    offset: int,
) -> None:
    """List events for a project.

    Examples:
        hostkit events list myapp
        hostkit events list myapp --category deploy
        hostkit events list myapp --category deploy,health
        hostkit events list myapp --level error
        hostkit events list myapp --since 1h
        hostkit events list myapp --since 24h --category deploy
        hostkit events list myapp --since "2025-12-15" --until "2025-12-16"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EventService()

    try:
        events_list = service.query(
            project_name=project,
            category=category,
            level=level.upper() if level else None,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

        total_count = service.count(
            project_name=project,
            category=category,
            level=level.upper() if level else None,
            since=since,
            until=until,
        )

        if ctx.obj.get("json_mode"):
            formatter.success(
                data={
                    "project": project,
                    "filters": {
                        "category": category,
                        "level": level,
                        "since": since,
                        "until": until,
                    },
                    "total_count": total_count,
                    "returned_count": len(events_list),
                    "events": [e.to_dict() for e in events_list],
                },
                message=f"Retrieved {len(events_list)} events",
            )
        else:
            if not events_list:
                click.echo(f"No events found for {project}")
                if category or level or since or until:
                    click.echo("Try adjusting your filters.")
                return

            click.echo(f"Events for {project} ({len(events_list)} of {total_count}):")
            click.echo("-" * 80)

            for event in events_list:
                # Format timestamp
                timestamp = event.created_at[:19] if event.created_at else ""

                # Color by level
                level_color = _get_level_color(event.level)

                # Build event line
                click.echo(
                    f"{timestamp} "
                    f"[{click.style(event.level.ljust(8), fg=level_color)}] "
                    f"[{event.category.ljust(10)}] "
                    f"{event.message}"
                )

                # Show data if present and in verbose mode
                if event.data and ctx.obj.get("verbose"):
                    import json

                    click.echo(f"                              Data: {json.dumps(event.data)}")

    except EventServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@events.command(name="show")
@click.argument("event_id", type=int)
@click.pass_context
def show_event(ctx: click.Context, event_id: int) -> None:
    """Show details of a specific event.

    Examples:
        hostkit events show 123
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EventService()

    event = service.get(event_id)

    if not event:
        formatter.error(
            code="EVENT_NOT_FOUND",
            message=f"Event {event_id} not found",
        )
        return

    if ctx.obj.get("json_mode"):
        formatter.success(
            data=event.to_dict(),
            message=f"Event {event_id} details",
        )
    else:
        click.echo(f"Event #{event.id}")
        click.echo("-" * 40)
        click.echo(f"  Project:    {event.project_name}")
        click.echo(f"  Category:   {event.category}")
        click.echo(f"  Type:       {event.event_type}")
        click.echo(f"  Level:      {click.style(event.level, fg=_get_level_color(event.level))}")
        click.echo(f"  Message:    {event.message}")
        click.echo(f"  Created At: {event.created_at}")
        click.echo(f"  Created By: {event.created_by or 'N/A'}")

        if event.data:
            click.echo()
            click.echo("  Data:")
            import json

            for key, value in event.data.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                click.echo(f"    {key}: {value}")


@events.command(name="stats")
@click.argument("project")
@click.option("--since", default="24h", help="Time window for stats (default: 24h)")
@click.pass_context
@project_access("project")
def event_stats(ctx: click.Context, project: str, since: str) -> None:
    """Show event statistics for a project.

    Examples:
        hostkit events stats myapp
        hostkit events stats myapp --since 7d
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EventService()

    try:
        # Get counts by category
        categories = [
            "deploy",
            "health",
            "auth",
            "migrate",
            "cron",
            "worker",
            "service",
            "checkpoint",
            "alert",
            "sandbox",
            "environment",
            "project",
        ]
        stats = {}

        for cat in categories:
            count = service.count(project_name=project, category=cat, since=since)
            if count > 0:
                stats[cat] = count

        # Get counts by level
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        level_stats = {}
        for level in levels:
            # Count events at exactly this level (not "and above")
            count = service.count(project_name=project, level=level, since=since)
            # Subtract counts from higher levels to get exact count
            level_stats[level] = count

        # Calculate exact level counts (the DB query gives "level and above")
        exact_level_counts = {}
        for i, level in enumerate(levels):
            if i == len(levels) - 1:
                exact_level_counts[level] = level_stats[level]
            else:
                exact_level_counts[level] = level_stats[level] - level_stats[levels[i + 1]]

        total = service.count(project_name=project, since=since)

        if ctx.obj.get("json_mode"):
            formatter.success(
                data={
                    "project": project,
                    "since": since,
                    "total_events": total,
                    "by_category": stats,
                    "by_level": exact_level_counts,
                },
                message=f"Event statistics for {project}",
            )
        else:
            click.echo(f"Event statistics for {project} (since {since}):")
            click.echo("-" * 40)
            click.echo(f"  Total events: {total}")
            click.echo()

            if stats:
                click.echo("  By category:")
                for cat, count in sorted(stats.items(), key=lambda x: -x[1]):
                    click.echo(f"    {cat.ljust(12)}: {count}")
            else:
                click.echo("  No events in this time window")

            click.echo()
            click.echo("  By level:")
            for level in levels:
                count = exact_level_counts[level]
                if count > 0:
                    color = _get_level_color(level)
                    click.echo(f"    {click.style(level.ljust(8), fg=color)}: {count}")

    except EventServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@events.command(name="cleanup")
@click.option("--older-than", default=30, help="Delete events older than N days (default: 30)")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
def cleanup_events(ctx: click.Context, older_than: int, force: bool) -> None:
    """Delete old events across all projects.

    Examples:
        hostkit events cleanup --force
        hostkit events cleanup --older-than 7 --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = EventService()

    if not force:
        formatter.error(
            code="FORCE_REQUIRED",
            message="Cleanup requires --force flag",
            suggestion="Add --force to confirm the operation",
        )
        return

    deleted = service.cleanup(older_than_days=older_than)

    formatter.success(
        data={"deleted_count": deleted, "older_than_days": older_than},
        message=f"Deleted {deleted} events older than {older_than} days",
    )


def _get_level_color(level: str) -> str:
    """Get color for event level."""
    level_colors = {
        "DEBUG": "blue",
        "INFO": "green",
        "WARNING": "yellow",
        "WARN": "yellow",
        "ERROR": "red",
        "CRITICAL": "red",
        "FATAL": "red",
    }
    return level_colors.get(level.upper(), "white")

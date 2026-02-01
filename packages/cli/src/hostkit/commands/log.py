"""Log management commands for HostKit CLI."""

from dataclasses import asdict

import click

from hostkit.access import project_access, root_only
from hostkit.output import OutputFormatter, format_bytes
from hostkit.services.log_service import LogService, LogServiceError


@click.group(name="log")
@click.pass_context
def log(ctx: click.Context) -> None:
    """Log management commands.

    View, search, and export logs from HostKit projects.
    Combines application logs, error logs, and systemd journal.
    """
    ctx.ensure_object(dict)


@log.command(name="show")
@click.argument("project")
@click.option("-n", "--lines", default=100, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow log output in real-time")
@click.option(
    "-l",
    "--level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        case_sensitive=False,
    ),
    help="Minimum log level to show",
)
@click.option(
    "-s",
    "--source",
    multiple=True,
    help="Log sources to include (app.log, error.log, journal)",
)
@click.option(
    "--since",
    help="Show logs since time (e.g., '1h', '24h', '7d', '2025-12-15')",
)
@click.option(
    "--until",
    help="Show logs until time (e.g., 'now', '2025-12-15')",
)
@click.pass_context
@project_access("project")
def show(
    ctx: click.Context,
    project: str,
    lines: int,
    follow: bool,
    level: str | None,
    source: tuple[str, ...],
    since: str | None,
    until: str | None,
) -> None:
    """Show logs for a project.

    Examples:
        hostkit log show myapp
        hostkit log show myapp --lines 50
        hostkit log show myapp --follow
        hostkit log show myapp --level ERROR
        hostkit log show myapp --source app.log --source error.log
        hostkit log show myapp --since 1h
        hostkit log show myapp --since 24h --level ERROR
        hostkit log show myapp --since "2025-12-15" --until "2025-12-16"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        sources = list(source) if source else None

        if follow:
            # Real-time streaming mode
            if ctx.obj.get("json_mode"):
                formatter.error(
                    code="FOLLOW_NOT_SUPPORTED",
                    message="Follow mode not supported with JSON output",
                    suggestion="Use without --json flag for streaming",
                )

            click.echo(f"Following logs for {project}... (Ctrl+C to stop)")
            click.echo("-" * 60)

            try:
                for entry in service.tail_logs(project, sources):
                    # Format output line
                    level_color = _get_level_color(entry.level)
                    timestamp = entry.timestamp[:19] if entry.timestamp else ""

                    if entry.level:
                        click.echo(
                            f"{timestamp} [{click.style(entry.level, fg=level_color)}] "
                            f"[{entry.source}] {entry.message}"
                        )
                    else:
                        click.echo(f"{timestamp} [{entry.source}] {entry.message}")
            except KeyboardInterrupt:
                click.echo("\nStopped following logs.")
                return
        else:
            # Static log retrieval
            entries = service.get_aggregated_logs(
                project, lines=lines, level=level, sources=sources, since=since, until=until
            )

            if ctx.obj.get("json_mode"):
                formatter.success(
                    data={
                        "project": project,
                        "lines": len(entries),
                        "entries": [asdict(e) for e in entries],
                    },
                    message=f"Retrieved {len(entries)} log entries",
                )
            else:
                if not entries:
                    click.echo(f"No log entries found for {project}")
                    return

                click.echo(f"Logs for {project} (last {len(entries)} entries):")
                click.echo("-" * 60)

                for entry in reversed(entries):  # Show oldest first
                    level_color = _get_level_color(entry.level)
                    timestamp = entry.timestamp[:19] if entry.timestamp else ""

                    if entry.level:
                        click.echo(
                            f"{timestamp} [{click.style(entry.level, fg=level_color)}] "
                            f"[{entry.source}] {entry.message}"
                        )
                    else:
                        click.echo(f"{timestamp} [{entry.source}] {entry.message}")

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="search")
@click.argument("project")
@click.argument("pattern")
@click.option(
    "-c",
    "--context",
    default=2,
    help="Lines of context around matches",
)
@click.option("-f", "--file", "files", multiple=True, help="Specific log files to search")
@click.option("-i", "--ignore-case/--case-sensitive", default=True, help="Case sensitivity")
@click.pass_context
@project_access("project")
def search(
    ctx: click.Context,
    project: str,
    pattern: str,
    context: int,
    files: tuple[str, ...],
    ignore_case: bool,
) -> None:
    """Search logs for a pattern.

    Supports regex patterns for advanced matching.

    Examples:
        hostkit log search myapp "error"
        hostkit log search myapp "Exception.*timeout" --context 5
        hostkit log search myapp "404" --file access.log
        hostkit log search myapp "ERROR" --case-sensitive
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        results = service.search_logs(
            project,
            pattern,
            context=context,
            files=list(files) if files else None,
            case_sensitive=not ignore_case,
        )

        if ctx.obj.get("json_mode"):
            formatter.success(
                data={
                    "project": project,
                    "pattern": pattern,
                    "matches": len(results),
                    "results": [asdict(r) for r in results],
                },
                message=f"Found {len(results)} matches",
            )
        else:
            if not results:
                click.echo(f"No matches found for pattern '{pattern}' in {project} logs")
                return

            click.echo(f"Found {len(results)} matches for '{pattern}':")
            click.echo()

            for i, result in enumerate(results, 1):
                # File and line info
                click.echo(
                    click.style(f"--- Match {i}: {result.file}:{result.line_number} ---", fg="cyan")
                )

                # Context before
                for line in result.context_before:
                    click.echo(f"  {click.style(line, dim=True)}")

                # Matching line (highlighted)
                click.echo(f"  {click.style(result.match, fg='yellow', bold=True)}")

                # Context after
                for line in result.context_after:
                    click.echo(f"  {click.style(line, dim=True)}")

                click.echo()

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="export")
@click.argument("project")
@click.argument("output", type=click.Path())
@click.option("--since", help="Export logs since (e.g., '1 hour ago', '2025-01-01')")
@click.option("--until", help="Export logs until (e.g., 'now', '2025-01-02')")
@click.option("--compress/--no-compress", default=True, help="Compress output with gzip")
@click.option("--include-journal/--no-journal", default=True, help="Include systemd journal")
@click.pass_context
@project_access("project")
def export(
    ctx: click.Context,
    project: str,
    output: str,
    since: str | None,
    until: str | None,
    compress: bool,
    include_journal: bool,
) -> None:
    """Export logs to a file.

    Combines all log sources into a single file for archival or analysis.

    Examples:
        hostkit log export myapp ./myapp-logs.txt
        hostkit log export myapp ./logs.txt.gz --compress
        hostkit log export myapp ./logs.txt --since "1 day ago"
        hostkit log export myapp ./logs.txt --since "2025-01-01" --until "2025-01-02"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        result = service.export_logs(
            project,
            output,
            since=since,
            until=until,
            compress=compress,
            include_journal=include_journal,
        )

        result["size_human"] = format_bytes(result["size"])

        formatter.success(
            data=result,
            message=f"Exported logs to {result['output_file']}",
        )

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="files")
@click.argument("project")
@click.pass_context
@project_access("project")
def files(ctx: click.Context, project: str) -> None:
    """List log files for a project.

    Examples:
        hostkit log files myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        log_files = service.get_log_files(project)

        if ctx.obj.get("json_mode"):
            formatter.success(
                data={"project": project, "files": log_files},
                message=f"Found {len(log_files)} log files",
            )
        else:
            if not log_files:
                click.echo(f"No log files found for {project}")
                return

            formatter.table(
                data=[
                    {
                        "name": f["name"],
                        "size": format_bytes(f["size"]),
                        "modified": f["modified"][:19],
                        "compressed": "Yes" if f["compressed"] else "No",
                    }
                    for f in log_files
                ],
                columns=[
                    ("name", "File"),
                    ("size", "Size"),
                    ("modified", "Modified"),
                    ("compressed", "Compressed"),
                ],
                title=f"Log files for {project}",
            )

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="stats")
@click.argument("project")
@click.pass_context
@project_access("project")
def stats(ctx: click.Context, project: str) -> None:
    """Show log statistics for a project.

    Examples:
        hostkit log stats myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        log_stats = service.get_log_stats(project)

        data = {
            "project": project,
            "total_size": log_stats.total_size,
            "total_size_human": format_bytes(log_stats.total_size),
            "file_count": log_stats.file_count,
            "oldest_entry": log_stats.oldest_entry,
            "newest_entry": log_stats.newest_entry,
            "error_count_24h": log_stats.error_count_24h,
            "warning_count_24h": log_stats.warning_count_24h,
        }

        if ctx.obj.get("json_mode"):
            formatter.success(data=data, message="Log statistics retrieved")
        else:
            click.echo(f"Log statistics for {project}:")
            click.echo("-" * 40)
            click.echo(f"  Total size:    {data['total_size_human']}")
            click.echo(f"  File count:    {data['file_count']}")
            click.echo(f"  Oldest entry:  {data['oldest_entry'] or 'N/A'}")
            click.echo(f"  Newest entry:  {data['newest_entry'] or 'N/A'}")
            click.echo()
            click.echo("  Recent activity:")

            error_style = "red" if data["error_count_24h"] > 0 else "green"
            warning_style = "yellow" if data["warning_count_24h"] > 0 else "green"

            click.echo(
                f"    Errors (24h):   {click.style(str(data['error_count_24h']), fg=error_style)}"
            )
            warn_count = click.style(str(data["warning_count_24h"]), fg=warning_style)
            click.echo(f"    Warnings (24h): {warn_count}")

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="clear")
@click.argument("project")
@click.option("--older-than", default=0, help="Only clear logs older than N days")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_access("project")
def clear(
    ctx: click.Context,
    project: str,
    older_than: int,
    force: bool,
) -> None:
    """Clear log files for a project.

    Examples:
        hostkit log clear myapp --force
        hostkit log clear myapp --older-than 7 --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        if not force:
            formatter.error(
                code="FORCE_REQUIRED",
                message="Clearing logs requires --force flag",
                suggestion="Add --force to confirm the operation",
            )

        result = service.clear_logs(project, older_than_days=older_than)

        result["cleared_size_human"] = format_bytes(result["cleared_size"])

        formatter.success(
            data=result,
            message=(
                f"Cleared {len(result['cleared_files'])} files ({result['cleared_size_human']})"
            ),
        )

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="setup")
@click.argument("project")
@click.pass_context
@root_only
def setup(ctx: click.Context, project: str) -> None:
    """Set up log directories for a project.

    Creates centralized log directory and symlink from home directory.
    Usually called automatically during project creation.

    Examples:
        hostkit log setup myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        result = service.setup_log_directory(project)
        formatter.success(
            data=result,
            message=f"Log directories configured for {project}",
        )

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


@log.command(name="setup-rotation")
@click.pass_context
@root_only
def setup_rotation(ctx: click.Context) -> None:
    """Set up logrotate for all HostKit projects.

    Configures daily log rotation with 7-day retention and compression.

    Examples:
        hostkit log setup-rotation
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = LogService()

    try:
        result = service.setup_logrotate()
        formatter.success(
            data=result,
            message="Logrotate configured for HostKit projects",
        )

    except LogServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)


def _get_level_color(level: str | None) -> str:
    """Get color for log level."""
    if not level:
        return "white"

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


# Alias: allow `hostkit log myapp` as shorthand for `hostkit log show myapp`
@log.command(name="view", hidden=True)
@click.argument("project")
@click.option("-n", "--lines", default=100, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
@click.pass_context
@project_access("project")
def view(ctx: click.Context, project: str, lines: int, follow: bool) -> None:
    """Alias for 'log show'."""
    ctx.invoke(
        show,
        project=project,
        lines=lines,
        follow=follow,
        level=None,
        source=(),
        since=None,
        until=None,
    )

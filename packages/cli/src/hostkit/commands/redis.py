"""Redis management commands for HostKit."""

import click

from hostkit.output import OutputFormatter, format_uptime
from hostkit.services.redis_service import RedisService, RedisServiceError


@click.group()
@click.pass_context
def redis(ctx: click.Context) -> None:
    """Manage Redis cache for projects.

    Each project is assigned a Redis database (0-49) for isolation.
    """
    pass


@redis.command("info")
@click.pass_context
def redis_info(ctx: click.Context) -> None:
    """Show Redis server information.

    Displays server version, memory usage, connected clients,
    and key counts per database.

    Example:
        hostkit redis info
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = RedisService()

    try:
        info = service.get_info()

        data = {
            "version": info.version,
            "uptime": info.uptime_seconds,
            "uptime_human": format_uptime(info.uptime_seconds),
            "connected_clients": info.connected_clients,
            "used_memory": info.used_memory,
            "used_memory_peak": info.used_memory_peak,
            "total_keys": info.total_keys,
            "databases": info.databases,
        }

        if ctx.obj["json_mode"]:
            formatter.success(message="Redis server info", data=data)
        else:
            click.echo("\nRedis Server Info")
            click.echo("=" * 50)
            click.echo(f"  Version:          {info.version}")
            click.echo(f"  Uptime:           {format_uptime(info.uptime_seconds)}")
            click.echo(f"  Connected:        {info.connected_clients} client(s)")
            click.echo(f"  Memory Used:      {info.used_memory}")
            click.echo(f"  Memory Peak:      {info.used_memory_peak}")
            click.echo(f"  Total Keys:       {info.total_keys}")

            if info.databases:
                click.echo("\nDatabases with Keys:")
                click.echo("-" * 30)
                for db_num, key_count in sorted(info.databases.items()):
                    # Try to find project using this db
                    from hostkit.database import get_db

                    hostkit_db = get_db()
                    projects = hostkit_db.list_projects()
                    project_name = None
                    for p in projects:
                        if p.get("redis_db") == db_num:
                            project_name = p["name"]
                            break

                    project_info = f" ({project_name})" if project_name else ""
                    click.echo(f"  db{db_num}: {key_count} key(s){project_info}")
            else:
                click.echo("\n  No keys in any database")

    except RedisServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@redis.command("keys")
@click.argument("project")
@click.option("--pattern", "-p", default="*", help="Key pattern to match (default: *)")
@click.option("--limit", "-l", default=100, help="Maximum keys to return (default: 100)")
@click.pass_context
def redis_keys(ctx: click.Context, project: str, pattern: str, limit: int) -> None:
    """List keys in a project's Redis database.

    Shows keys matching the pattern (default: all keys).
    Use patterns like 'cache:*' or 'session:*' to filter.

    Example:
        hostkit redis keys myapp
        hostkit redis keys myapp --pattern "cache:*"
        hostkit redis keys myapp --limit 50
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = RedisService()

    try:
        result = service.get_keys(project, pattern=pattern, limit=limit)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Keys in {project}'s Redis database",
                data=result,
            )
        else:
            click.echo(f"\nRedis Keys for '{project}' (db{result['redis_db']})")
            click.echo("=" * 50)
            click.echo(f"  Pattern: {pattern}")
            click.echo(f"  Total in DB: {result['total_in_db']} key(s)")
            click.echo(f"  Matching: {result['count']} key(s)")

            if result["limited"]:
                click.echo(f"  (Limited to {limit})")

            if result["keys"]:
                click.echo("\nKeys:")
                click.echo("-" * 50)
                for key in result["keys"]:
                    click.echo(f"  {key}")
            else:
                click.echo("\n  No keys found matching pattern")

    except RedisServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@redis.command("flush")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm flushing all keys")
@click.pass_context
def redis_flush(ctx: click.Context, project: str, force: bool) -> None:
    """Flush all keys in a project's Redis database.

    This deletes ALL keys in the project's assigned Redis database.
    Useful for clearing cache or resetting state.

    Example:
        hostkit redis flush myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = RedisService()

    try:
        result = service.flush_db(project, force=force)

        formatter.success(
            message=f"Flushed Redis database for '{project}'",
            data={
                "project": result["project"],
                "redis_db": result["redis_db"],
                "keys_deleted": result["keys_deleted"],
            },
        )

    except RedisServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

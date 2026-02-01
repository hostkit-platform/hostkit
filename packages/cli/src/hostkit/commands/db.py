"""Database management commands for HostKit."""

import os
import subprocess
import sys

import click

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.database_service import DatabaseService, DatabaseServiceError


@click.group()
@click.pass_context
def db(ctx: click.Context) -> None:
    """Manage PostgreSQL databases for projects.

    Each project can have its own PostgreSQL database with isolated credentials.
    """
    pass


@db.command("create")
@click.argument("project")
@click.pass_context
def db_create(ctx: click.Context, project: str) -> None:
    """Create a PostgreSQL database for a project.

    Creates a new database named {project}_db and a role named {project}_user
    with a secure random password. The DATABASE_URL is automatically added
    to the project's .env file.

    Example:
        hostkit db create myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        # Check project exists
        from hostkit.database import get_db

        hostkit_db = get_db()
        if not hostkit_db.get_project(project):
            formatter.error(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
            raise SystemExit(1)

        # Create database
        credentials = service.create_database(project)

        # Update project .env file
        service.update_project_env(project, credentials)

        formatter.success(
            message=f"Database created for project '{project}'",
            data={
                "project": project,
                "database": credentials.database,
                "username": credentials.username,
                "host": credentials.host,
                "port": credentials.port,
                "env_updated": True,
            },
        )

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("delete")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
def db_delete(ctx: click.Context, project: str, force: bool) -> None:
    """Delete a PostgreSQL database for a project.

    Terminates active connections and drops the database and role.
    Removes DATABASE_URL from the project's .env file.

    Example:
        hostkit db delete myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        # Delete database
        service.delete_database(project, force=force)

        # Remove from .env file
        service.remove_database_from_env(project)

        formatter.success(
            message=f"Database deleted for project '{project}'",
            data={
                "project": project,
                "database": f"{project}_db",
                "role": f"{project}_user",
                "env_updated": True,
            },
        )

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("list")
@click.pass_context
def db_list(ctx: click.Context) -> None:
    """List all HostKit-managed PostgreSQL databases.

    Shows database name, owner, size, and active connections.

    Example:
        hostkit db list
        hostkit --json db list
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        databases = service.list_databases()

        if not databases:
            formatter.success(
                message="No databases found",
                data={"databases": [], "count": 0},
            )
            return

        data = {
            "databases": [
                {
                    "name": db.name,
                    "owner": db.owner,
                    "size": db.size,
                    "connections": db.connections,
                    "project": db.project,
                }
                for db in databases
            ],
            "count": len(databases),
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Found {len(databases)} database(s)", data=data)
        else:
            # Pretty table output
            click.echo("\nDatabases:")
            click.echo("-" * 70)
            click.echo(
                f"{'DATABASE':<20} {'PROJECT':<15} {'SIZE':<10} {'CONNS':<8} {'OWNER':<15}"
            )
            click.echo("-" * 70)

            for db in databases:
                click.echo(
                    f"{db.name:<20} {db.project or '-':<15} {db.size:<10} {db.connections:<8} {db.owner:<15}"
                )

            click.echo("-" * 70)
            click.echo(f"Total: {len(databases)} database(s)")

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("info")
@click.argument("project")
@click.pass_context
@project_access("project")
def db_info(ctx: click.Context, project: str) -> None:
    """Show detailed information about a project's database.

    Example:
        hostkit db info myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        db_info = service.get_database_info(project)

        data = {
            "database": db_info.name,
            "project": db_info.project,
            "owner": db_info.owner,
            "size": db_info.size,
            "connections": db_info.connections,
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Database info for '{project}'", data=data)
        else:
            click.echo(f"\nDatabase: {db_info.name}")
            click.echo("-" * 40)
            click.echo(f"  Project:     {db_info.project}")
            click.echo(f"  Owner:       {db_info.owner}")
            click.echo(f"  Size:        {db_info.size}")
            click.echo(f"  Connections: {db_info.connections}")

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("backup")
@click.argument("project")
@click.pass_context
@project_owner("project")
def db_backup(ctx: click.Context, project: str) -> None:
    """Create a backup of a project's database.

    Uses pg_dump to create a compressed backup stored in /backups/{project}/db/.
    The backup is registered in HostKit's internal database.

    Example:
        hostkit db backup myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        result = service.backup_database(project)

        # Format size
        size_mb = result["size"] / (1024 * 1024)
        size_str = f"{size_mb:.2f} MB" if size_mb >= 1 else f"{result['size']} bytes"

        formatter.success(
            message=f"Database backup created for '{project}'",
            data={
                "backup_id": result["backup_id"],
                "project": result["project"],
                "path": result["path"],
                "size": size_str,
                "size_bytes": result["size"],
                "created_at": result["created_at"],
            },
        )

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("restore")
@click.argument("project")
@click.argument("backup_path")
@click.pass_context
@project_owner("project")
def db_restore(ctx: click.Context, project: str, backup_path: str) -> None:
    """Restore a project's database from a backup.

    Drops the existing database, recreates it, and restores from the backup file.
    Supports both plain SQL and gzipped backups.

    Example:
        hostkit db restore myapp /backups/myapp/db/myapp_db_20250101_120000.sql.gz
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        result = service.restore_database(project, backup_path)

        formatter.success(
            message=f"Database restored for '{project}'",
            data={
                "project": result["project"],
                "database": result["database"],
                "backup_file": result["backup_file"],
                "restored_at": result["restored_at"],
            },
        )

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("shell")
@click.argument("project")
@click.option(
    "-c",
    "--command",
    "sql_command",
    default=None,
    help="Execute a single SQL command and exit (non-interactive)",
)
@click.pass_context
@project_access("project")
def db_shell(ctx: click.Context, project: str, sql_command: str | None) -> None:
    """Open an interactive psql session for a project's database.

    Connects as the project's database user. The password is read from
    the project's .env file.

    Examples:
        hostkit db shell myapp
        hostkit db shell myapp -c "SELECT * FROM users LIMIT 5"
        hostkit db shell myapp --command "UPDATE users SET role='admin' WHERE id=1"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        shell_cmd = service.get_shell_command(project)

        # Try to get password from .env
        env_path = f"/home/{project}/.env"
        password = None

        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith("DATABASE_URL="):
                        url = line.strip().split("=", 1)[1]
                        if "://" in url and "@" in url:
                            auth_part = url.split("://")[1].split("@")[0]
                            if ":" in auth_part:
                                # URL decode the password
                                from urllib.parse import unquote_plus

                                password = unquote_plus(auth_part.split(":", 1)[1])
                        break
        except (OSError, IndexError):
            pass

        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password

        # Non-interactive mode with -c flag
        if sql_command:
            # Add -c flag to psql command
            cmd_with_sql = shell_cmd + ["-c", sql_command]

            result = subprocess.run(
                cmd_with_sql,
                env=env,
                capture_output=True,
                text=True,
            )

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"SQL executed on '{project}'",
                    data={
                        "project": project,
                        "database": f"{project}_db",
                        "command": sql_command,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.returncode,
                        "success": result.returncode == 0,
                    },
                )
            else:
                if result.stdout:
                    click.echo(result.stdout)
                if result.stderr:
                    click.echo(result.stderr, err=True)

            if result.returncode != 0:
                raise SystemExit(result.returncode)
            return

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Shell command for '{project}'",
                data={
                    "command": shell_cmd,
                    "project": project,
                    "database": f"{project}_db",
                    "user": f"{project}_user",
                },
            )
            return

        # Interactive mode - exec into psql
        click.echo(f"Connecting to {project}_db as {project}_user...")
        click.echo("Type \\q to exit.\n")

        # Replace current process with psql
        os.execvpe(shell_cmd[0], shell_cmd, env)

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("enable-extension")
@click.argument("project")
@click.argument("extension")
@click.pass_context
@project_access("project")
def db_enable_extension(ctx: click.Context, project: str, extension: str) -> None:
    """Enable a PostgreSQL extension in a project's database.

    Supported extensions:
      vector     - pgvector for embeddings/AI similarity search
      postgis    - Geospatial database extension
      pg_trgm    - Trigram text similarity and indexing
      uuid-ossp  - UUID generation functions
      pgcrypto   - Cryptographic functions
      hstore     - Key-value store
      citext     - Case-insensitive text type
      unaccent   - Text search accent removal
      fuzzystrmatch - Fuzzy string matching
      tablefunc  - Crosstab and table functions

    Example:
        hostkit db enable-extension myapp vector
        hostkit db enable-extension myapp pg_trgm
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        result = service.enable_extension(project, extension)

        if result["status"] == "already_enabled":
            formatter.success(
                message=f"Extension '{extension}' is already enabled in '{project}'",
                data=result,
            )
        else:
            formatter.success(
                message=f"Extension '{extension}' enabled in '{project}'",
                data=result,
            )

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("extensions")
@click.argument("project")
@click.pass_context
@project_access("project")
def db_extensions(ctx: click.Context, project: str) -> None:
    """List enabled PostgreSQL extensions in a project's database.

    Example:
        hostkit db extensions myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        extensions = service.list_extensions(project)

        if not extensions:
            formatter.success(
                message=f"No extensions enabled in '{project}'",
                data={"extensions": [], "count": 0},
            )
            return

        data = {
            "project": project,
            "extensions": extensions,
            "count": len(extensions),
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Found {len(extensions)} extension(s)", data=data)
        else:
            click.echo(f"\nExtensions in {project}_db:")
            click.echo("-" * 60)
            click.echo(f"{'EXTENSION':<20} {'VERSION':<10} {'DESCRIPTION':<30}")
            click.echo("-" * 60)

            for ext in extensions:
                click.echo(
                    f"{ext['name']:<20} {ext['version']:<10} {ext['description']:<30}"
                )

            click.echo("-" * 60)
            click.echo(f"Total: {len(extensions)} extension(s)")

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@db.command("migrate")
@click.argument("project")
@click.option(
    "-f",
    "--file",
    "sql_file",
    default=None,
    help="Path to a specific SQL migration file to run",
)
@click.option(
    "-d",
    "--dir",
    "migrations_dir",
    default=None,
    help="Path to directory containing .sql migration files (runs all in order)",
)
@click.pass_context
@project_owner("project")
def db_migrate(
    ctx: click.Context, project: str, sql_file: str | None, migrations_dir: str | None
) -> None:
    """Run SQL migrations against a project's database.

    Executes SQL files as the project's database user, ensuring created objects
    (tables, indexes, etc.) are owned by the project.

    Use --file to run a single migration, or --dir to run all .sql files in a
    directory (sorted alphabetically, e.g., 001_init.sql, 002_users.sql).

    Examples:
        hostkit db migrate myapp --file /home/myapp/migrations/009_skills.sql
        hostkit db migrate myapp --dir /home/myapp/migrations/
        hostkit db migrate myapp -f ./schema.sql
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DatabaseService()

    try:
        result = service.run_migration(
            project_name=project,
            sql_file=sql_file,
            migrations_dir=migrations_dir,
        )

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Migrations completed for '{project}'",
                data=result,
            )
        else:
            click.echo(f"\nMigrations for {project}:")
            click.echo("-" * 60)

            for migration in result["results"]:
                status = "✓" if migration["success"] else "✗"
                click.echo(f"  {status} {migration['name']}")
                if migration["stderr"] and not migration["success"]:
                    click.echo(f"    Error: {migration['stderr'][:200]}")

            click.echo("-" * 60)
            click.echo(f"Total: {result['migrations_run']} migration(s) run")

    except DatabaseServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

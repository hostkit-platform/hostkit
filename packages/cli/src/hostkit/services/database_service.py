"""PostgreSQL database management service for HostKit."""

import os
import secrets
import string
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta


CapabilitiesRegistry.register_service(ServiceMeta(
    name="database",
    description="PostgreSQL 15 database",
    provision_flag="--with-db",
    enable_command=None,
    env_vars_provided=["DATABASE_URL"],
    related_commands=["db create", "db backup", "db shell", "db restore"],
))


@dataclass
class DatabaseCredentials:
    """Database connection credentials."""

    database: str
    username: str
    password: str
    host: str
    port: int

    @property
    def connection_url(self) -> str:
        """Generate PostgreSQL connection URL."""
        # URL-encode password for special characters
        from urllib.parse import quote_plus

        encoded_password = quote_plus(self.password)
        return f"postgresql://{self.username}:{encoded_password}@{self.host}:{self.port}/{self.database}"


@dataclass
class DatabaseInfo:
    """Information about a PostgreSQL database."""

    name: str
    owner: str
    size: str
    connections: int
    project: str | None = None


class DatabaseServiceError(Exception):
    """Base exception for database service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class DatabaseService:
    """Service for managing PostgreSQL databases for HostKit projects."""

    def __init__(self) -> None:
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

    def _get_admin_connection(self) -> psycopg2.extensions.connection:
        """Get a connection to PostgreSQL as the admin user."""
        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database="postgres",
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            return conn
        except psycopg2.OperationalError as e:
            raise DatabaseServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL: {e}",
                suggestion="Check PostgreSQL is running and credentials are correct",
            )

    def _db_name(self, project_name: str) -> str:
        """Generate database name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project_name.replace("-", "_")
        return f"{safe_name}_db"

    def _role_name(self, project_name: str) -> str:
        """Generate role name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project_name.replace("-", "_")
        return f"{safe_name}_user"

    def _validate_identifier(self, name: str) -> None:
        """Validate a PostgreSQL identifier to prevent SQL injection.

        Accepts project names with hyphens (they get converted to underscores
        for PostgreSQL identifiers).
        """
        import re

        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = name.replace("-", "_")
        if not re.match(r"^[a-z][a-z0-9_]{0,62}$", safe_name):
            raise DatabaseServiceError(
                code="INVALID_IDENTIFIER",
                message=f"Invalid identifier: {name}",
                suggestion="Use lowercase letters, numbers, hyphens, and underscores only",
            )

    def create_database(self, project_name: str) -> DatabaseCredentials:
        """Create a PostgreSQL database and role for a project."""
        db_name = self._db_name(project_name)
        role_name = self._role_name(project_name)

        # Validate identifiers
        self._validate_identifier(project_name)

        # Check if database already exists
        if self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_EXISTS",
                message=f"Database '{db_name}' already exists",
                suggestion="Delete existing database first or use a different project name",
            )

        password = generate_secure_password()

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                # Create role
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(
                        sql.Identifier(role_name)
                    ),
                    [password],
                )

                # Create database owned by the role
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(db_name), sql.Identifier(role_name)
                    )
                )

                # Grant all privileges on database to the role
                cur.execute(
                    sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                        sql.Identifier(db_name), sql.Identifier(role_name)
                    )
                )

        finally:
            conn.close()

        return DatabaseCredentials(
            database=db_name,
            username=role_name,
            password=password,
            host=self.config.postgres_host,
            port=self.config.postgres_port,
        )

    def delete_database(self, project_name: str, force: bool = False) -> None:
        """Delete a PostgreSQL database and role for a project."""
        if not force:
            raise DatabaseServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a database requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        db_name = self._db_name(project_name)
        role_name = self._role_name(project_name)

        # Validate identifiers
        self._validate_identifier(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database '{db_name}' does not exist",
                suggestion="Run 'hostkit db list' to see available databases",
            )

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                # Terminate active connections to the database
                cur.execute(
                    sql.SQL(
                        """
                        SELECT pg_terminate_backend(pg_stat_activity.pid)
                        FROM pg_stat_activity
                        WHERE pg_stat_activity.datname = %s
                        AND pid <> pg_backend_pid()
                        """
                    ),
                    [db_name],
                )

                # Drop database
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name))
                )

                # Drop role
                cur.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )

        finally:
            conn.close()

    def database_exists(self, project_name: str) -> bool:
        """Check if a database exists for a project."""
        db_name = self._db_name(project_name)

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", [db_name]
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def list_databases(self) -> list[DatabaseInfo]:
        """List all HostKit-managed databases."""
        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                # Get all databases that match our naming convention
                cur.execute(
                    """
                    SELECT
                        d.datname AS name,
                        pg_catalog.pg_get_userbyid(d.datdba) AS owner,
                        pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) AS size,
                        (SELECT count(*) FROM pg_stat_activity WHERE datname = d.datname) AS connections
                    FROM pg_catalog.pg_database d
                    WHERE d.datname LIKE '%_db'
                    AND d.datname NOT IN ('postgres', 'template0', 'template1')
                    ORDER BY d.datname
                    """
                )
                rows = cur.fetchall()

                databases = []
                for row in rows:
                    # Extract project name from database name
                    db_name = row[0]
                    project_name = (
                        db_name[:-3] if db_name.endswith("_db") else None
                    )

                    databases.append(
                        DatabaseInfo(
                            name=row[0],
                            owner=row[1],
                            size=row[2],
                            connections=row[3],
                            project=project_name,
                        )
                    )

                return databases
        finally:
            conn.close()

    def get_database_info(self, project_name: str) -> DatabaseInfo:
        """Get information about a specific database."""
        db_name = self._db_name(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Run 'hostkit db create {project}' first",
            )

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        d.datname AS name,
                        pg_catalog.pg_get_userbyid(d.datdba) AS owner,
                        pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) AS size,
                        (SELECT count(*) FROM pg_stat_activity WHERE datname = d.datname) AS connections
                    FROM pg_catalog.pg_database d
                    WHERE d.datname = %s
                    """,
                    [db_name],
                )
                row = cur.fetchone()

                if not row:
                    raise DatabaseServiceError(
                        code="DATABASE_NOT_FOUND",
                        message=f"Database '{db_name}' not found",
                        suggestion="Run 'hostkit db list' to see available databases",
                    )

                return DatabaseInfo(
                    name=row[0],
                    owner=row[1],
                    size=row[2],
                    connections=row[3],
                    project=project_name,
                )
        finally:
            conn.close()

    def backup_database(self, project_name: str) -> dict[str, Any]:
        """Create a backup of a project's database using pg_dump."""
        db_name = self._db_name(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Run 'hostkit db create {project}' first",
            )

        # Create backup directory
        backup_dir = self.config.backup_dir / project_name / "db"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{db_name}_{timestamp}.sql.gz"
        backup_path = backup_dir / backup_filename

        # Build pg_dump command
        env = os.environ.copy()
        if self._admin_password:
            env["PGPASSWORD"] = self._admin_password

        pg_dump_cmd = [
            "pg_dump",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self._admin_user,
            "-d", db_name,
            "--no-owner",
            "--no-acl",
        ]

        try:
            # Run pg_dump and pipe to gzip
            pg_dump_proc = subprocess.Popen(
                pg_dump_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            gzip_proc = subprocess.Popen(
                ["gzip"],
                stdin=pg_dump_proc.stdout,
                stdout=open(backup_path, "wb"),
                stderr=subprocess.PIPE,
            )

            pg_dump_proc.stdout.close()  # Allow pg_dump to receive SIGPIPE
            _, gzip_stderr = gzip_proc.communicate()
            _, pg_dump_stderr = pg_dump_proc.communicate()

            if pg_dump_proc.returncode != 0:
                raise DatabaseServiceError(
                    code="BACKUP_FAILED",
                    message=f"pg_dump failed: {pg_dump_stderr.decode()}",
                    suggestion="Check database exists and credentials are correct",
                )

            if gzip_proc.returncode != 0:
                raise DatabaseServiceError(
                    code="BACKUP_FAILED",
                    message=f"gzip failed: {gzip_stderr.decode()}",
                    suggestion="Check disk space and permissions",
                )

        except FileNotFoundError:
            raise DatabaseServiceError(
                code="COMMAND_NOT_FOUND",
                message="pg_dump or gzip not found",
                suggestion="Ensure PostgreSQL client tools are installed",
            )

        # Get backup file size
        backup_size = backup_path.stat().st_size

        # Record backup in HostKit database
        backup_id = f"db-{project_name}-{timestamp}"
        self.hostkit_db.create_backup_record(
            backup_id=backup_id,
            project=project_name,
            backup_type="db",
            path=str(backup_path),
            size_bytes=backup_size,
        )

        return {
            "backup_id": backup_id,
            "project": project_name,
            "path": str(backup_path),
            "size": backup_size,
            "created_at": datetime.utcnow().isoformat(),
        }

    def restore_database(self, project_name: str, backup_path: str) -> dict[str, Any]:
        """Restore a database from a backup file."""
        db_name = self._db_name(project_name)
        role_name = self._role_name(project_name)

        backup_file = Path(backup_path)
        if not backup_file.exists():
            raise DatabaseServiceError(
                code="BACKUP_NOT_FOUND",
                message=f"Backup file not found: {backup_path}",
                suggestion="Run 'hostkit backup list {project}' to see available backups",
            )

        # Check if database exists; if so, drop and recreate
        if self.database_exists(project_name):
            # Terminate connections and drop database
            conn = self._get_admin_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            """
                            SELECT pg_terminate_backend(pg_stat_activity.pid)
                            FROM pg_stat_activity
                            WHERE pg_stat_activity.datname = %s
                            AND pid <> pg_backend_pid()
                            """
                        ),
                        [db_name],
                    )
                    cur.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {}").format(
                            sql.Identifier(db_name)
                        )
                    )
                    # Recreate database
                    cur.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(db_name), sql.Identifier(role_name)
                        )
                    )
            finally:
                conn.close()
        else:
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Create the database first with 'hostkit db create {project}'",
            )

        # Build restore command
        env = os.environ.copy()
        if self._admin_password:
            env["PGPASSWORD"] = self._admin_password

        psql_cmd = [
            "psql",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self._admin_user,
            "-d", db_name,
            "-q",  # Quiet mode
        ]

        try:
            # Determine if file is gzipped
            if str(backup_file).endswith(".gz"):
                # Decompress and pipe to psql
                gunzip_proc = subprocess.Popen(
                    ["gunzip", "-c", str(backup_file)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                psql_proc = subprocess.Popen(
                    psql_cmd,
                    stdin=gunzip_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )

                gunzip_proc.stdout.close()
                _, psql_stderr = psql_proc.communicate()
                _, gunzip_stderr = gunzip_proc.communicate()

                if gunzip_proc.returncode != 0:
                    raise DatabaseServiceError(
                        code="RESTORE_FAILED",
                        message=f"gunzip failed: {gunzip_stderr.decode()}",
                        suggestion="Check backup file is valid gzip",
                    )
            else:
                # Plain SQL file
                with open(backup_file) as f:
                    psql_proc = subprocess.Popen(
                        psql_cmd,
                        stdin=f,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                    )
                    _, psql_stderr = psql_proc.communicate()

            if psql_proc.returncode != 0:
                raise DatabaseServiceError(
                    code="RESTORE_FAILED",
                    message=f"psql restore failed: {psql_stderr.decode()}",
                    suggestion="Check backup file format and database credentials",
                )

        except FileNotFoundError as e:
            raise DatabaseServiceError(
                code="COMMAND_NOT_FOUND",
                message=f"Required command not found: {e}",
                suggestion="Ensure PostgreSQL client tools are installed",
            )

        return {
            "project": project_name,
            "database": db_name,
            "backup_file": str(backup_file),
            "restored_at": datetime.utcnow().isoformat(),
        }

    def get_shell_command(self, project_name: str) -> list[str]:
        """Get the psql command to open an interactive shell for a project's database."""
        db_name = self._db_name(project_name)
        role_name = self._role_name(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Run 'hostkit db create {project}' first",
            )

        # Read password from project's .env file
        env_path = Path(f"/home/{project_name}/.env")
        password = None

        if env_path.exists():
            try:
                with open(env_path) as f:
                    for line in f:
                        if line.startswith("DATABASE_URL="):
                            # Extract password from URL
                            url = line.strip().split("=", 1)[1]
                            if "://" in url and "@" in url:
                                auth_part = url.split("://")[1].split("@")[0]
                                if ":" in auth_part:
                                    password = auth_part.split(":")[1]
                            break
            except (OSError, IndexError):
                pass

        cmd = [
            "psql",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", role_name,
            "-d", db_name,
        ]

        # Note: password will need to be provided via PGPASSWORD env var
        # The caller should handle setting this from the .env file
        return cmd

    def update_project_env(
        self, project_name: str, credentials: DatabaseCredentials
    ) -> None:
        """Update a project's .env file with database credentials."""
        env_path = Path(f"/home/{project_name}/.env")

        if not env_path.exists():
            raise DatabaseServiceError(
                code="ENV_NOT_FOUND",
                message=f"Environment file not found: {env_path}",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Read existing content
        with open(env_path) as f:
            lines = f.readlines()

        # Update or add DATABASE_URL
        new_lines = []
        found_db_url = False

        for line in lines:
            if line.startswith("DATABASE_URL=") or line.startswith("# DATABASE_URL="):
                new_lines.append(f"DATABASE_URL={credentials.connection_url}\n")
                found_db_url = True
            else:
                new_lines.append(line)

        if not found_db_url:
            # Add DATABASE_URL after REDIS_URL if it exists, otherwise at end
            inserted = False
            final_lines = []
            for line in new_lines:
                final_lines.append(line)
                if line.startswith("REDIS_URL=") and not inserted:
                    final_lines.append(f"\n# PostgreSQL (HostKit managed)\nDATABASE_URL={credentials.connection_url}\n")
                    inserted = True
            if not inserted:
                final_lines.append(f"\n# PostgreSQL (HostKit managed)\nDATABASE_URL={credentials.connection_url}\n")
            new_lines = final_lines

        # Write back
        with open(env_path, "w") as f:
            f.writelines(new_lines)

        # Ensure correct ownership
        subprocess.run(
            ["chown", f"{project_name}:{project_name}", str(env_path)],
            check=True,
            capture_output=True,
        )

    def remove_database_from_env(self, project_name: str) -> None:
        """Remove DATABASE_URL from a project's .env file."""
        env_path = Path(f"/home/{project_name}/.env")

        if not env_path.exists():
            return  # Nothing to do

        # Read existing content
        with open(env_path) as f:
            lines = f.readlines()

        # Remove DATABASE_URL line
        new_lines = []
        skip_next_empty = False

        for line in lines:
            if line.startswith("DATABASE_URL="):
                skip_next_empty = True
                continue
            if line.startswith("# PostgreSQL (HostKit managed)"):
                continue
            if skip_next_empty and line.strip() == "":
                skip_next_empty = False
                continue
            new_lines.append(line)
            skip_next_empty = False

        # Write back
        with open(env_path, "w") as f:
            f.writelines(new_lines)

    # Supported PostgreSQL extensions (whitelist for security)
    SUPPORTED_EXTENSIONS = {
        "vector": "pgvector - Vector similarity search for embeddings/AI",
        "postgis": "PostGIS - Geospatial database extension",
        "pg_trgm": "pg_trgm - Trigram text similarity and indexing",
        "uuid-ossp": "uuid-ossp - UUID generation functions",
        "pgcrypto": "pgcrypto - Cryptographic functions",
        "hstore": "hstore - Key-value store within PostgreSQL",
        "citext": "citext - Case-insensitive text type",
        "unaccent": "unaccent - Text search dictionary for accent removal",
        "fuzzystrmatch": "fuzzystrmatch - Fuzzy string matching",
        "tablefunc": "tablefunc - Crosstab and other table functions",
    }

    def enable_extension(self, project_name: str, extension: str) -> dict[str, Any]:
        """Enable a PostgreSQL extension in a project's database.

        Args:
            project_name: Project name
            extension: Extension name (must be in SUPPORTED_EXTENSIONS)

        Returns:
            Dict with extension info
        """
        db_name = self._db_name(project_name)

        # Validate extension is supported
        if extension not in self.SUPPORTED_EXTENSIONS:
            supported_list = ", ".join(sorted(self.SUPPORTED_EXTENSIONS.keys()))
            raise DatabaseServiceError(
                code="UNSUPPORTED_EXTENSION",
                message=f"Extension '{extension}' is not supported",
                suggestion=f"Supported extensions: {supported_list}",
            )

        # Check database exists
        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Create the database first with 'hostkit db create {project}'",
            )

        # Connect to the project's database as admin and enable extension
        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database=db_name,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            try:
                with conn.cursor() as cur:
                    # Check if extension is already enabled
                    cur.execute(
                        "SELECT 1 FROM pg_extension WHERE extname = %s",
                        [extension],
                    )
                    already_enabled = cur.fetchone() is not None

                    if already_enabled:
                        return {
                            "project": project_name,
                            "database": db_name,
                            "extension": extension,
                            "description": self.SUPPORTED_EXTENSIONS[extension],
                            "status": "already_enabled",
                        }

                    # Enable the extension
                    cur.execute(
                        sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                            sql.Identifier(extension)
                        )
                    )

                    return {
                        "project": project_name,
                        "database": db_name,
                        "extension": extension,
                        "description": self.SUPPORTED_EXTENSIONS[extension],
                        "status": "enabled",
                    }
            finally:
                conn.close()

        except psycopg2.Error as e:
            raise DatabaseServiceError(
                code="EXTENSION_ENABLE_FAILED",
                message=f"Failed to enable extension '{extension}': {e}",
                suggestion=f"Ensure the extension is installed on the server (apt install postgresql-16-{extension})",
            )

    def list_extensions(self, project_name: str) -> list[dict[str, Any]]:
        """List enabled extensions in a project's database.

        Args:
            project_name: Project name

        Returns:
            List of enabled extensions with their versions
        """
        db_name = self._db_name(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Create the database first with 'hostkit db create {project}'",
            )

        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database=db_name,
            )

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT extname, extversion
                        FROM pg_extension
                        WHERE extname != 'plpgsql'
                        ORDER BY extname
                        """
                    )
                    rows = cur.fetchall()

                    return [
                        {
                            "name": row[0],
                            "version": row[1],
                            "description": self.SUPPORTED_EXTENSIONS.get(row[0], ""),
                        }
                        for row in rows
                    ]
            finally:
                conn.close()

        except psycopg2.Error as e:
            raise DatabaseServiceError(
                code="LIST_EXTENSIONS_FAILED",
                message=f"Failed to list extensions: {e}",
                suggestion="Check database connection",
            )

    def run_migration(
        self,
        project_name: str,
        sql_file: str | None = None,
        migrations_dir: str | None = None,
    ) -> dict[str, Any]:
        """Run SQL migration file(s) against a project's database.

        Runs as the project's database user so created objects are owned by the project.
        If migrations_dir is provided, runs all .sql files in order. If sql_file is
        provided, runs just that file.

        Args:
            project_name: Project name
            sql_file: Path to specific SQL file to run
            migrations_dir: Path to directory containing .sql migration files

        Returns:
            Dict with migration results
        """
        db_name = self._db_name(project_name)
        role_name = self._role_name(project_name)

        if not self.database_exists(project_name):
            raise DatabaseServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database for project '{project_name}' does not exist",
                suggestion="Run 'hostkit db create {project}' first",
            )

        # Determine which files to run
        sql_files: list[Path] = []

        if sql_file:
            path = Path(sql_file)
            if not path.exists():
                raise DatabaseServiceError(
                    code="FILE_NOT_FOUND",
                    message=f"SQL file not found: {sql_file}",
                    suggestion="Check the file path exists",
                )
            sql_files = [path]
        elif migrations_dir:
            dir_path = Path(migrations_dir)
            if not dir_path.is_dir():
                raise DatabaseServiceError(
                    code="DIR_NOT_FOUND",
                    message=f"Migrations directory not found: {migrations_dir}",
                    suggestion="Check the directory path exists",
                )
            # Get all .sql files, sorted by name (assumes naming like 001_init.sql)
            sql_files = sorted(dir_path.glob("*.sql"))
            if not sql_files:
                raise DatabaseServiceError(
                    code="NO_MIGRATIONS",
                    message=f"No .sql files found in: {migrations_dir}",
                    suggestion="Add .sql migration files to the directory",
                )
        else:
            raise DatabaseServiceError(
                code="MISSING_ARGUMENT",
                message="Must provide either --file or --dir",
                suggestion="Use --file path/to/file.sql or --dir path/to/migrations/",
            )

        # Get project's database password from .env
        env_path = Path(f"/home/{project_name}/.env")
        password = None

        if env_path.exists():
            try:
                with open(env_path) as f:
                    for line in f:
                        if line.startswith("DATABASE_URL="):
                            url = line.strip().split("=", 1)[1]
                            if "://" in url and "@" in url:
                                auth_part = url.split("://")[1].split("@")[0]
                                if ":" in auth_part:
                                    from urllib.parse import unquote_plus
                                    password = unquote_plus(auth_part.split(":", 1)[1])
                            break
            except (OSError, IndexError):
                pass

        if not password:
            raise DatabaseServiceError(
                code="NO_PASSWORD",
                message=f"Could not read DATABASE_URL from {env_path}",
                suggestion="Ensure database is configured for this project",
            )

        # Run each migration file
        results: list[dict[str, Any]] = []
        env = os.environ.copy()
        env["PGPASSWORD"] = password

        for migration_file in sql_files:
            psql_cmd = [
                "psql",
                "-h", self.config.postgres_host,
                "-p", str(self.config.postgres_port),
                "-U", role_name,  # Run as project user, not admin
                "-d", db_name,
                "-v", "ON_ERROR_STOP=1",  # Stop on first error
                "-f", str(migration_file),
            ]

            try:
                result = subprocess.run(
                    psql_cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                )

                migration_result = {
                    "file": str(migration_file),
                    "name": migration_file.name,
                    "success": result.returncode == 0,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
                results.append(migration_result)

                if result.returncode != 0:
                    raise DatabaseServiceError(
                        code="MIGRATION_FAILED",
                        message=f"Migration failed: {migration_file.name}\n{result.stderr}",
                        suggestion="Fix the SQL error and retry",
                    )

            except FileNotFoundError:
                raise DatabaseServiceError(
                    code="PSQL_NOT_FOUND",
                    message="psql command not found",
                    suggestion="Ensure PostgreSQL client is installed",
                )

        return {
            "project": project_name,
            "database": db_name,
            "migrations_run": len(results),
            "results": results,
            "success": all(r["success"] for r in results),
        }

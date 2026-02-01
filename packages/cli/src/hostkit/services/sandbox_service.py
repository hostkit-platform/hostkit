"""Sandbox management service for HostKit.

Sandboxes are temporary, isolated clones of projects for safe experimentation.
"""

import json
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db


# Sandbox limits
MAX_SANDBOXES_PER_PROJECT = 3
DEFAULT_TTL_HOURS = 24


@dataclass
class SandboxInfo:
    """Information about a sandbox instance."""

    id: str
    sandbox_name: str
    source_project: str
    source_release: str | None
    port: int
    domain: str | None
    db_name: str | None
    status: str
    expires_at: str
    created_at: str
    created_by: str


@dataclass
class PromoteResult:
    """Result of promoting a sandbox."""

    success: bool
    source_project: str
    sandbox_name: str
    backup_id: str | None
    database_swapped: bool
    code_swapped: bool
    message: str


class SandboxServiceError(Exception):
    """Base exception for sandbox service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class SandboxService:
    """Service for managing sandbox instances."""

    def __init__(self) -> None:
        self.db = get_db()
        self.config = get_config()

    def create_sandbox(
        self,
        source_project: str,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        include_db: bool = True,
    ) -> SandboxInfo:
        """Create a sandbox from an existing project.

        Args:
            source_project: Name of the project to clone
            ttl_hours: Time-to-live in hours (default: 24)
            include_db: Whether to clone the database (default: True)

        Returns:
            SandboxInfo with sandbox details

        Raises:
            SandboxServiceError: If creation fails
        """
        # 1. Validate source project exists
        project = self.db.get_project(source_project)
        if not project:
            raise SandboxServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{source_project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # 2. Check sandbox limit
        sandbox_count = self.db.count_sandboxes_for_project(source_project)
        if sandbox_count >= MAX_SANDBOXES_PER_PROJECT:
            raise SandboxServiceError(
                code="SANDBOX_LIMIT_EXCEEDED",
                message=f"Maximum {MAX_SANDBOXES_PER_PROJECT} sandboxes per project",
                suggestion=f"Delete existing sandboxes with 'hostkit sandbox list {source_project}'",
            )

        # 3. Generate sandbox name and ID
        short_id = secrets.token_hex(2)  # 4 hex characters
        sandbox_name = f"{source_project}-sandbox-{short_id}"
        sandbox_id = secrets.token_hex(8)

        # Verify sandbox name is unique
        if self.db.get_sandbox(sandbox_name):
            # Extremely unlikely collision, try again
            short_id = secrets.token_hex(2)
            sandbox_name = f"{source_project}-sandbox-{short_id}"

        # 4. Calculate expiration
        expires_at = (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat()

        # 5. Get creator
        created_by = os.environ.get("USER", "root")
        if created_by == "root":
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                created_by = sudo_user

        # 6. Get current release path
        current_release = self.db.get_current_release(source_project)
        source_release = current_release["release_name"] if current_release else None

        # 7. Assign port
        port = self.db.get_next_port()

        # 8. Create database record first (so we can track cleanup on failure)
        sandbox_record = self.db.create_sandbox(
            sandbox_id=sandbox_id,
            sandbox_name=sandbox_name,
            source_project=source_project,
            port=port,
            expires_at=expires_at,
            created_by=created_by,
            source_release=source_release,
            status="creating",
        )

        try:
            # 9. Create Linux user
            self._create_linux_user(sandbox_name)

            # 10. Create directory structure
            self._create_sandbox_directories(sandbox_name, project["runtime"])

            # 11. Copy code from source project
            self._clone_code(source_project, sandbox_name, source_release)

            # 12. Clone .env file and update port
            self._clone_env_file(source_project, sandbox_name, port)

            # 13. Clone database if requested and source has one
            db_name = None
            if include_db:
                db_name = self._clone_database(source_project, sandbox_name)
                if db_name:
                    self.db.update_sandbox(sandbox_name, db_name=db_name)

            # 14. Create systemd service
            self._create_systemd_service(sandbox_name, project["runtime"], port)

            # 15. Create domain (nip.io)
            domain = f"{sandbox_name}.{self.config.vps_ip}.nip.io"
            self._create_nginx_config(sandbox_name, domain, port)
            self.db.update_sandbox(sandbox_name, domain=domain)

            # 16. Create log directory
            self._create_log_directory(sandbox_name)

            # 17. Start the sandbox service
            self._start_service(sandbox_name)

            # 18. Update status to active
            self.db.update_sandbox(sandbox_name, status="active")

            # Return updated sandbox info
            sandbox = self.db.get_sandbox(sandbox_name)
            return self._to_sandbox_info(sandbox)

        except Exception as e:
            # Cleanup on failure
            self.db.update_sandbox(sandbox_name, status="failed")
            self._cleanup_sandbox_resources(sandbox_name)
            raise SandboxServiceError(
                code="SANDBOX_CREATE_FAILED",
                message=f"Failed to create sandbox: {e}",
                suggestion="Check system logs for details",
            )

    def list_sandboxes(
        self,
        source_project: str | None = None,
        include_expired: bool = False,
    ) -> list[SandboxInfo]:
        """List sandboxes, optionally filtered by source project.

        Args:
            source_project: Filter by source project (optional)
            include_expired: Include expired sandboxes (default: False)

        Returns:
            List of SandboxInfo objects
        """
        sandboxes = self.db.list_sandboxes(
            source_project=source_project,
            include_expired=include_expired,
        )
        return [self._to_sandbox_info(s) for s in sandboxes]

    def get_sandbox(self, sandbox_name: str) -> SandboxInfo:
        """Get information about a specific sandbox.

        Args:
            sandbox_name: Name of the sandbox

        Returns:
            SandboxInfo object

        Raises:
            SandboxServiceError: If sandbox not found
        """
        sandbox = self.db.get_sandbox(sandbox_name)
        if not sandbox:
            raise SandboxServiceError(
                code="SANDBOX_NOT_FOUND",
                message=f"Sandbox '{sandbox_name}' not found",
                suggestion="Run 'hostkit sandbox list' to see available sandboxes",
            )
        return self._to_sandbox_info(sandbox)

    def delete_sandbox(self, sandbox_name: str, force: bool = False) -> bool:
        """Delete a sandbox and all its resources.

        Args:
            sandbox_name: Name of the sandbox to delete
            force: Required to confirm deletion

        Returns:
            True if deleted successfully

        Raises:
            SandboxServiceError: If deletion fails
        """
        sandbox = self.db.get_sandbox(sandbox_name)
        if not sandbox:
            raise SandboxServiceError(
                code="SANDBOX_NOT_FOUND",
                message=f"Sandbox '{sandbox_name}' not found",
                suggestion="Run 'hostkit sandbox list' to see available sandboxes",
            )

        if not force:
            raise SandboxServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a sandbox requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        # Cleanup all resources
        self._cleanup_sandbox_resources(sandbox_name)

        # Delete database record
        self.db.delete_sandbox(sandbox_name)

        return True

    def promote_sandbox(
        self,
        sandbox_name: str,
        dry_run: bool = False,
    ) -> PromoteResult:
        """Promote a sandbox to replace its source project.

        This operation:
        1. Stops the source project
        2. Creates a backup of the source
        3. Swaps the database (renames)
        4. Swaps the code (copy sandbox to source)
        5. Restarts the source project
        6. Deletes the sandbox

        Args:
            sandbox_name: Name of the sandbox to promote
            dry_run: Preview changes without applying (default: False)

        Returns:
            PromoteResult with operation details

        Raises:
            SandboxServiceError: If promotion fails
        """
        sandbox = self.db.get_sandbox(sandbox_name)
        if not sandbox:
            raise SandboxServiceError(
                code="SANDBOX_NOT_FOUND",
                message=f"Sandbox '{sandbox_name}' not found",
                suggestion="Run 'hostkit sandbox list' to see available sandboxes",
            )

        if sandbox["status"] not in ("active", "creating"):
            raise SandboxServiceError(
                code="INVALID_SANDBOX_STATE",
                message=f"Cannot promote sandbox in '{sandbox['status']}' state",
                suggestion="Sandbox must be 'active' to promote",
            )

        source_project = sandbox["source_project"]
        project = self.db.get_project(source_project)
        if not project:
            raise SandboxServiceError(
                code="SOURCE_PROJECT_MISSING",
                message=f"Source project '{source_project}' no longer exists",
                suggestion="Cannot promote to a deleted project",
            )

        if dry_run:
            return PromoteResult(
                success=True,
                source_project=source_project,
                sandbox_name=sandbox_name,
                backup_id=None,
                database_swapped=sandbox["db_name"] is not None,
                code_swapped=True,
                message="Dry run - no changes made",
            )

        # Mark sandbox as promoting
        self.db.update_sandbox(sandbox_name, status="promoting")

        backup_id = None
        db_swapped = False

        try:
            # 1. Stop source project
            self._stop_service(source_project)

            # 2. Create backup of source (optional, best effort)
            try:
                from hostkit.services.backup_service import BackupService
                backup_service = BackupService()
                backup = backup_service.create_backup(source_project, full=True)
                backup_id = backup.get("backup_id")
            except Exception:
                pass  # Backup is optional

            # 3. Swap database if sandbox has one
            if sandbox["db_name"]:
                self._swap_databases(source_project, sandbox_name)
                db_swapped = True

            # 4. Swap code
            self._swap_code(source_project, sandbox_name)

            # 5. Update env file port back to source
            self._update_env_port(source_project, project["port"])

            # 6. Restart source project
            self._start_service(source_project)

            # 7. Cleanup sandbox resources (but not the promoted code/db)
            self._stop_service(sandbox_name)
            self._remove_systemd_service(sandbox_name)
            self._remove_nginx_config(sandbox_name)
            self._delete_linux_user(sandbox_name)
            self._remove_log_directory(sandbox_name)

            # 8. Delete sandbox record
            self.db.delete_sandbox(sandbox_name)

            return PromoteResult(
                success=True,
                source_project=source_project,
                sandbox_name=sandbox_name,
                backup_id=backup_id,
                database_swapped=db_swapped,
                code_swapped=True,
                message=f"Sandbox '{sandbox_name}' promoted to '{source_project}'",
            )

        except Exception as e:
            # Try to restore source project
            self.db.update_sandbox(sandbox_name, status="failed")
            try:
                self._start_service(source_project)
            except Exception:
                pass
            raise SandboxServiceError(
                code="PROMOTE_FAILED",
                message=f"Failed to promote sandbox: {e}",
                suggestion="Check system logs. Source project may need manual recovery.",
            )

    def cleanup_expired(self) -> list[str]:
        """Delete all expired sandboxes.

        Returns:
            List of deleted sandbox names
        """
        expired = self.db.get_expired_sandboxes()
        deleted = []

        for sandbox in expired:
            try:
                self._cleanup_sandbox_resources(sandbox["sandbox_name"])
                self.db.update_sandbox(sandbox["sandbox_name"], status="expired")
                self.db.delete_sandbox(sandbox["sandbox_name"])
                deleted.append(sandbox["sandbox_name"])
            except Exception:
                # Log error but continue with other sandboxes
                pass

        return deleted

    def extend_ttl(self, sandbox_name: str, hours: int) -> SandboxInfo:
        """Extend a sandbox's TTL.

        Args:
            sandbox_name: Name of the sandbox
            hours: Hours to extend by

        Returns:
            Updated SandboxInfo

        Raises:
            SandboxServiceError: If sandbox not found or invalid state
        """
        sandbox = self.db.get_sandbox(sandbox_name)
        if not sandbox:
            raise SandboxServiceError(
                code="SANDBOX_NOT_FOUND",
                message=f"Sandbox '{sandbox_name}' not found",
                suggestion="Run 'hostkit sandbox list' to see available sandboxes",
            )

        if sandbox["status"] != "active":
            raise SandboxServiceError(
                code="INVALID_SANDBOX_STATE",
                message=f"Cannot extend sandbox in '{sandbox['status']}' state",
                suggestion="Sandbox must be 'active' to extend TTL",
            )

        # Calculate new expiration
        current_expires = datetime.fromisoformat(sandbox["expires_at"])
        new_expires = current_expires + timedelta(hours=hours)

        self.db.update_sandbox(sandbox_name, expires_at=new_expires.isoformat())

        updated = self.db.get_sandbox(sandbox_name)
        return self._to_sandbox_info(updated)

    # =========================================================================
    # Private helper methods
    # =========================================================================

    def _to_sandbox_info(self, sandbox: dict[str, Any]) -> SandboxInfo:
        """Convert database record to SandboxInfo."""
        return SandboxInfo(
            id=sandbox["id"],
            sandbox_name=sandbox["sandbox_name"],
            source_project=sandbox["source_project"],
            source_release=sandbox.get("source_release"),
            port=sandbox["port"],
            domain=sandbox.get("domain"),
            db_name=sandbox.get("db_name"),
            status=sandbox["status"],
            expires_at=sandbox["expires_at"],
            created_at=sandbox["created_at"],
            created_by=sandbox["created_by"],
        )

    def _create_linux_user(self, name: str) -> None:
        """Create a Linux user for the sandbox."""
        subprocess.run(
            [
                "useradd",
                "--system",
                "--create-home",
                "--home-dir", f"/home/{name}",
                "--shell", "/bin/bash",
                name,
            ],
            check=True,
            capture_output=True,
        )

    def _delete_linux_user(self, name: str) -> None:
        """Delete a Linux user and their home directory."""
        try:
            subprocess.run(
                ["userdel", "--remove", name],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # User might not exist

    def _create_sandbox_directories(self, name: str, runtime: str) -> None:
        """Create directory structure for sandbox."""
        home = Path(f"/home/{name}")

        dirs = [home / "app", home / "logs"]

        if runtime == "python":
            dirs.append(home / "venv")
        elif runtime == "node":
            dirs.append(home / "node_modules")

        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

        self._chown_recursive(home, name)

    def _clone_code(
        self,
        source_project: str,
        sandbox_name: str,
        source_release: str | None,
    ) -> None:
        """Clone code from source project to sandbox."""
        source_app = Path(f"/home/{source_project}/app")
        dest_app = Path(f"/home/{sandbox_name}/app")

        if source_app.exists():
            # Use rsync for efficient copying
            subprocess.run(
                [
                    "rsync", "-a", "--delete",
                    f"{source_app}/",
                    f"{dest_app}/",
                ],
                check=True,
                capture_output=True,
            )
            self._chown_recursive(dest_app, sandbox_name)

        # Also copy venv or node_modules if they exist
        for subdir in ["venv", "node_modules"]:
            source_dir = Path(f"/home/{source_project}/{subdir}")
            dest_dir = Path(f"/home/{sandbox_name}/{subdir}")
            if source_dir.exists():
                subprocess.run(
                    [
                        "rsync", "-a", "--delete",
                        f"{source_dir}/",
                        f"{dest_dir}/",
                    ],
                    check=True,
                    capture_output=True,
                )
                self._chown_recursive(dest_dir, sandbox_name)

    def _clone_env_file(
        self,
        source_project: str,
        sandbox_name: str,
        port: int,
    ) -> None:
        """Clone .env file from source and update port."""
        source_env = Path(f"/home/{source_project}/.env")
        dest_env = Path(f"/home/{sandbox_name}/.env")

        if source_env.exists():
            content = source_env.read_text()

            # Update PROJECT_NAME
            if "PROJECT_NAME=" in content:
                import re
                content = re.sub(
                    r"PROJECT_NAME=.*",
                    f"PROJECT_NAME={sandbox_name}",
                    content,
                )

            # Update PORT
            if "PORT=" in content:
                import re
                content = re.sub(r"PORT=\d+", f"PORT={port}", content)
            else:
                content += f"\nPORT={port}\n"

            dest_env.write_text(content)
        else:
            # Create minimal env file
            content = f"""# Sandbox environment
PROJECT_NAME={sandbox_name}
PORT={port}
HOST=127.0.0.1
"""
            dest_env.write_text(content)

        # Set ownership and permissions
        subprocess.run(["chown", f"{sandbox_name}:{sandbox_name}", str(dest_env)], check=True)
        subprocess.run(["chmod", "600", str(dest_env)], check=True)

    def _clone_database(self, source_project: str, sandbox_name: str) -> str | None:
        """Clone database from source project.

        Returns the new database name if successful, None if source has no database.
        """
        try:
            from hostkit.services.database_service import DatabaseService, DatabaseServiceError

            db_service = DatabaseService()

            # Check if source has a database
            if not db_service.database_exists(source_project):
                return None

            # Create new database for sandbox
            sandbox_db_name = f"{sandbox_name}_db"
            credentials = db_service.create_database(sandbox_name)

            # Dump source database and restore to sandbox
            source_db_name = f"{source_project}_db"
            admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
            admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

            env = os.environ.copy()
            if admin_password:
                env["PGPASSWORD"] = admin_password

            # pg_dump | psql pipeline
            pg_dump_cmd = [
                "pg_dump",
                "-h", "localhost",
                "-U", admin_user,
                "-d", source_db_name,
                "--no-owner",
                "--no-acl",
            ]

            psql_cmd = [
                "psql",
                "-h", "localhost",
                "-U", admin_user,
                "-d", sandbox_db_name,
                "-q",
            ]

            pg_dump = subprocess.Popen(
                pg_dump_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            psql = subprocess.Popen(
                psql_cmd,
                stdin=pg_dump.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            pg_dump.stdout.close()
            psql.communicate()

            # Update sandbox .env with new database URL
            sandbox_env = Path(f"/home/{sandbox_name}/.env")
            if sandbox_env.exists():
                content = sandbox_env.read_text()
                content = content.replace(
                    f"DATABASE_URL=postgresql://{source_project}",
                    f"DATABASE_URL=postgresql://{sandbox_name}",
                )
                # Also update any direct references
                content = content.replace(source_db_name, sandbox_db_name)
                sandbox_env.write_text(content)

            return sandbox_db_name

        except Exception:
            return None

    def _create_systemd_service(
        self,
        sandbox_name: str,
        runtime: str,
        port: int,
    ) -> None:
        """Create systemd service for sandbox."""
        from hostkit.services.project_service import (
            SYSTEMD_TEMPLATE,
            DEFAULT_START_COMMANDS,
        )

        start_command = DEFAULT_START_COMMANDS.get(
            runtime, DEFAULT_START_COMMANDS["python"]
        )
        start_command = start_command.format(project_name=sandbox_name)

        service_content = SYSTEMD_TEMPLATE.format(
            project_name=sandbox_name,
            start_command=start_command,
            resource_limits="",  # Sandboxes use no resource limits by default
        )

        service_path = Path(f"/etc/systemd/system/hostkit-{sandbox_name}.service")
        service_path.write_text(service_content)

        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _remove_systemd_service(self, name: str) -> None:
        """Remove systemd service."""
        service_path = Path(f"/etc/systemd/system/hostkit-{name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _create_nginx_config(self, sandbox_name: str, domain: str, port: int) -> None:
        """Create Nginx configuration for sandbox."""
        from datetime import datetime
        from jinja2 import Template

        template = Template("""# Managed by HostKit Sandbox
# Sandbox: {{ sandbox_name }}
# Generated: {{ timestamp }}

server {
    listen 80;
    server_name {{ domain }};

    location / {
        proxy_pass http://127.0.0.1:{{ port }};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 86400;
    }
}
""")

        config = template.render(
            sandbox_name=sandbox_name,
            domain=domain,
            port=port,
            timestamp=datetime.utcnow().isoformat(),
        )

        config_path = Path(f"/etc/nginx/sites-available/{sandbox_name}")
        config_path.write_text(config)

        # Enable site
        enabled_path = Path(f"/etc/nginx/sites-enabled/{sandbox_name}")
        if enabled_path.exists():
            enabled_path.unlink()
        enabled_path.symlink_to(config_path)

        # Reload nginx
        subprocess.run(
            ["nginx", "-t"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "reload", "nginx"],
            check=True,
            capture_output=True,
        )

    def _remove_nginx_config(self, name: str) -> None:
        """Remove Nginx configuration."""
        config_path = Path(f"/etc/nginx/sites-available/{name}")
        enabled_path = Path(f"/etc/nginx/sites-enabled/{name}")

        if enabled_path.exists():
            enabled_path.unlink()
        if config_path.exists():
            config_path.unlink()

        try:
            subprocess.run(
                ["systemctl", "reload", "nginx"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

    def _create_log_directory(self, name: str) -> None:
        """Create log directory for sandbox."""
        log_dir = Path(f"/var/log/projects/{name}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "app.log").touch()
        (log_dir / "error.log").touch()
        self._chown_recursive(log_dir, name)

    def _remove_log_directory(self, name: str) -> None:
        """Remove log directory."""
        log_dir = Path(f"/var/log/projects/{name}")
        if log_dir.exists():
            shutil.rmtree(log_dir)

    def _start_service(self, name: str) -> None:
        """Start systemd service."""
        subprocess.run(
            ["systemctl", "start", f"hostkit-{name}"],
            check=True,
            capture_output=True,
        )

    def _stop_service(self, name: str) -> None:
        """Stop systemd service."""
        try:
            subprocess.run(
                ["systemctl", "stop", f"hostkit-{name}"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

    def _chown_recursive(self, path: Path, user: str) -> None:
        """Recursively change ownership."""
        subprocess.run(
            ["chown", "-R", f"{user}:{user}", str(path)],
            check=True,
            capture_output=True,
        )

    def _cleanup_sandbox_resources(self, sandbox_name: str) -> None:
        """Clean up all resources for a sandbox."""
        # Stop service
        self._stop_service(sandbox_name)

        # Remove systemd service
        self._remove_systemd_service(sandbox_name)

        # Remove nginx config
        self._remove_nginx_config(sandbox_name)

        # Delete database if exists
        sandbox = self.db.get_sandbox(sandbox_name)
        if sandbox and sandbox.get("db_name"):
            try:
                from hostkit.services.database_service import DatabaseService
                db_service = DatabaseService()
                db_service.delete_database(sandbox_name, force=True)
            except Exception:
                pass

        # Delete Linux user and home directory
        self._delete_linux_user(sandbox_name)

        # Remove log directory
        self._remove_log_directory(sandbox_name)

    def _swap_databases(self, source_project: str, sandbox_name: str) -> None:
        """Swap databases between source and sandbox.

        Uses rename approach for atomic swap.
        """
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        source_db = f"{source_project}_db"
        sandbox_db = f"{sandbox_name}_db"
        temp_db = f"{source_project}_db_old"

        conn = psycopg2.connect(
            host="localhost",
            user=admin_user,
            password=admin_password,
            database="postgres",
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        try:
            with conn.cursor() as cur:
                # Terminate connections to both databases
                for db in [source_db, sandbox_db]:
                    cur.execute(
                        """
                        SELECT pg_terminate_backend(pg_stat_activity.pid)
                        FROM pg_stat_activity
                        WHERE pg_stat_activity.datname = %s
                        AND pid <> pg_backend_pid()
                        """,
                        [db],
                    )

                # Rename source to temp
                cur.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(source_db),
                        sql.Identifier(temp_db),
                    )
                )

                # Rename sandbox to source
                cur.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                        sql.Identifier(sandbox_db),
                        sql.Identifier(source_db),
                    )
                )

                # Drop old source database
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        sql.Identifier(temp_db)
                    )
                )

        finally:
            conn.close()

    def _swap_code(self, source_project: str, sandbox_name: str) -> None:
        """Copy sandbox code to source project."""
        sandbox_app = Path(f"/home/{sandbox_name}/app")
        source_app = Path(f"/home/{source_project}/app")

        if sandbox_app.exists():
            subprocess.run(
                [
                    "rsync", "-a", "--delete",
                    f"{sandbox_app}/",
                    f"{source_app}/",
                ],
                check=True,
                capture_output=True,
            )
            self._chown_recursive(source_app, source_project)

        # Also copy venv or node_modules
        for subdir in ["venv", "node_modules"]:
            sandbox_dir = Path(f"/home/{sandbox_name}/{subdir}")
            source_dir = Path(f"/home/{source_project}/{subdir}")
            if sandbox_dir.exists():
                subprocess.run(
                    [
                        "rsync", "-a", "--delete",
                        f"{sandbox_dir}/",
                        f"{source_dir}/",
                    ],
                    check=True,
                    capture_output=True,
                )
                self._chown_recursive(source_dir, source_project)

    def _update_env_port(self, project_name: str, port: int) -> None:
        """Update PORT in .env file."""
        import re

        env_path = Path(f"/home/{project_name}/.env")
        if env_path.exists():
            content = env_path.read_text()
            content = re.sub(r"PORT=\d+", f"PORT={port}", content)
            env_path.write_text(content)

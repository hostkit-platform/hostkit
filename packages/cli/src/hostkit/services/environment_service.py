"""Environment management service for HostKit.

Environments allow projects to have separate staging/production configurations
with optional separate databases and deployment targets.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db

# Environment limits
MAX_ENVIRONMENTS_PER_PROJECT = 5


@dataclass
class EnvironmentInfo:
    """Information about an environment instance."""

    id: int
    project_name: str
    env_name: str
    linux_user: str
    port: int
    db_name: str | None
    share_parent_db: bool
    status: str
    created_at: str
    created_by: str


@dataclass
class PromoteResult:
    """Result of promoting between environments."""

    success: bool
    source_env: str
    target_env: str
    code_copied: bool
    db_copied: bool
    message: str


class EnvironmentServiceError(Exception):
    """Base exception for environment service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class EnvironmentService:
    """Service for managing project environments."""

    def __init__(self) -> None:
        self.db = get_db()
        self.config = get_config()

    def create_environment(
        self,
        project_name: str,
        env_name: str,
        with_db: bool = False,
        share_db: bool = False,
    ) -> EnvironmentInfo:
        """Create a new environment for a project.

        Args:
            project_name: Name of the parent project
            env_name: Name for the environment (e.g., 'staging', 'production')
            with_db: Create a separate database for this environment
            share_db: Share the parent project's database (mutually exclusive with with_db)

        Returns:
            EnvironmentInfo with environment details

        Raises:
            EnvironmentServiceError: If creation fails
        """
        # 1. Validate parent project exists
        project = self.db.get_project(project_name)
        if not project:
            raise EnvironmentServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # 2. Validate environment name
        if not re.match(r"^[a-z][a-z0-9_-]{1,30}$", env_name):
            raise EnvironmentServiceError(
                code="INVALID_ENV_NAME",
                message="Environment name must be 2-31 chars, lowercase, start with letter",
                suggestion="Example names: staging, production, dev",
            )

        # 3. Check if environment already exists
        existing = self.db.get_environment(project_name, env_name)
        if existing:
            raise EnvironmentServiceError(
                code="ENVIRONMENT_EXISTS",
                message=f"Environment '{env_name}' already exists for project '{project_name}'",
                suggestion=(
                    f"Run 'hostkit environment info {project_name} {env_name}' to see details"
                ),
            )

        # 4. Check environment limit
        env_count = self.db.count_environments_for_project(project_name)
        if env_count >= MAX_ENVIRONMENTS_PER_PROJECT:
            raise EnvironmentServiceError(
                code="ENVIRONMENT_LIMIT_EXCEEDED",
                message=f"Maximum {MAX_ENVIRONMENTS_PER_PROJECT} environments per project",
                suggestion=(
                    f"Delete existing environments with 'hostkit environment list {project_name}'"
                ),
            )

        # 5. Validate db options
        if with_db and share_db:
            raise EnvironmentServiceError(
                code="INVALID_OPTIONS",
                message="Cannot use both --with-db and --share-db",
                suggestion=(
                    "Choose one: --with-db for separate database,"
                    " --share-db to use project's database"
                ),
            )

        # 6. Generate linux user name
        linux_user = f"{project_name}-{env_name}"

        # Check user doesn't exist
        try:
            subprocess.run(
                ["id", linux_user],
                check=True,
                capture_output=True,
            )
            raise EnvironmentServiceError(
                code="USER_EXISTS",
                message=f"Linux user '{linux_user}' already exists",
                suggestion="Choose a different environment name",
            )
        except subprocess.CalledProcessError:
            pass  # User doesn't exist, which is what we want

        # 7. Get creator
        created_by = os.environ.get("USER", "root")
        if created_by == "root":
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                created_by = sudo_user

        # 8. Assign port
        port = self.db.get_next_port()

        # 9. Create database record first
        self.db.create_environment(
            project_name=project_name,
            env_name=env_name,
            linux_user=linux_user,
            port=port,
            share_parent_db=share_db,
            created_by=created_by,
        )

        try:
            # 10. Create Linux user
            self._create_linux_user(linux_user)

            # 11. Create directory structure
            self._create_environment_directories(linux_user, project["runtime"])

            # 12. Create .env file with environment port
            self._create_env_file(linux_user, env_name, port, project_name, share_db)

            # 13. Create database if requested
            db_name = None
            if with_db:
                db_name = self._create_database(project_name, env_name, linux_user)
                # Update the record with db_name
                self.db.delete_environment(project_name, env_name)
                self.db.create_environment(
                    project_name=project_name,
                    env_name=env_name,
                    linux_user=linux_user,
                    port=port,
                    db_name=db_name,
                    share_parent_db=False,
                    created_by=created_by,
                )

            # 14. Create systemd service
            self._create_systemd_service(linux_user, project["runtime"], port)

            # 15. Create nginx config with nip.io domain
            domain = f"{linux_user}.{self.config.vps_ip}.nip.io"
            self._create_nginx_config(linux_user, domain, port)

            # 16. Create log directory
            self._create_log_directory(linux_user)

            # 17. Create sudoers file for environment user
            self._create_sudoers(linux_user, project_name, env_name)

            # Return updated environment info
            env = self.db.get_environment(project_name, env_name)
            return self._to_environment_info(env)

        except Exception as e:
            # Cleanup on failure
            self._cleanup_environment_resources(linux_user, project_name, env_name)
            self.db.delete_environment(project_name, env_name)
            raise EnvironmentServiceError(
                code="ENVIRONMENT_CREATE_FAILED",
                message=f"Failed to create environment: {e}",
                suggestion="Check system logs for details",
            )

    def list_environments(self, project_name: str | None = None) -> list[EnvironmentInfo]:
        """List environments, optionally filtered by project.

        Args:
            project_name: Filter by project (optional)

        Returns:
            List of EnvironmentInfo objects
        """
        envs = self.db.list_environments(project_name)
        return [self._to_environment_info(e) for e in envs]

    def get_environment(self, project_name: str, env_name: str) -> EnvironmentInfo:
        """Get information about a specific environment.

        Args:
            project_name: Parent project name
            env_name: Environment name

        Returns:
            EnvironmentInfo object

        Raises:
            EnvironmentServiceError: If environment not found
        """
        env = self.db.get_environment(project_name, env_name)
        if not env:
            raise EnvironmentServiceError(
                code="ENVIRONMENT_NOT_FOUND",
                message=f"Environment '{env_name}' not found for project '{project_name}'",
                suggestion=(
                    f"Run 'hostkit environment list {project_name}' to see available environments"
                ),
            )
        return self._to_environment_info(env)

    def get_environment_details(self, project_name: str, env_name: str) -> dict[str, Any]:
        """Get detailed information about an environment.

        Args:
            project_name: Parent project name
            env_name: Environment name

        Returns:
            Dictionary with detailed environment info

        Raises:
            EnvironmentServiceError: If environment not found
        """
        env = self.get_environment(project_name, env_name)
        linux_user = env.linux_user

        # Get service status
        status = "unknown"
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"hostkit-{linux_user}"],
                capture_output=True,
                text=True,
            )
            status = result.stdout.strip()
        except Exception:
            pass

        # Calculate disk usage
        home_path = Path(f"/home/{linux_user}")
        disk_usage = 0
        if home_path.exists():
            try:
                result = subprocess.run(
                    ["du", "-sb", str(home_path)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    disk_usage = int(result.stdout.split()[0])
            except Exception:
                pass

        # Get domain
        domain = f"{linux_user}.{self.config.vps_ip}.nip.io"

        return {
            "project_name": env.project_name,
            "env_name": env.env_name,
            "linux_user": env.linux_user,
            "port": env.port,
            "db_name": env.db_name,
            "share_parent_db": env.share_parent_db,
            "status": status,
            "domain": domain,
            "home_directory": f"/home/{linux_user}",
            "service_name": f"hostkit-{linux_user}",
            "disk_usage_bytes": disk_usage,
            "created_at": env.created_at,
            "created_by": env.created_by,
        }

    def delete_environment(self, project_name: str, env_name: str, force: bool = False) -> bool:
        """Delete an environment and all its resources.

        Args:
            project_name: Parent project name
            env_name: Environment name
            force: Required to confirm deletion

        Returns:
            True if deleted successfully

        Raises:
            EnvironmentServiceError: If deletion fails
        """
        env = self.db.get_environment(project_name, env_name)
        if not env:
            raise EnvironmentServiceError(
                code="ENVIRONMENT_NOT_FOUND",
                message=f"Environment '{env_name}' not found for project '{project_name}'",
                suggestion=(
                    f"Run 'hostkit environment list {project_name}' to see available environments"
                ),
            )

        if not force:
            raise EnvironmentServiceError(
                code="FORCE_REQUIRED",
                message="Deleting an environment requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        linux_user = env["linux_user"]

        # Cleanup all resources
        self._cleanup_environment_resources(linux_user, project_name, env_name)

        # Delete database if environment has its own
        if env.get("db_name"):
            self._delete_database(project_name, env_name)

        # Delete database record
        self.db.delete_environment(project_name, env_name)

        return True

    def promote(
        self,
        project_name: str,
        source_env: str,
        target_env: str,
        with_db: bool = False,
        dry_run: bool = False,
    ) -> PromoteResult:
        """Promote code (and optionally data) from one environment to another.

        This operation:
        1. Stops the target environment
        2. Copies code from source to target
        3. Optionally copies database (dump + restore)
        4. Restarts the target environment

        Args:
            project_name: Parent project name
            source_env: Source environment name (e.g., 'staging')
            target_env: Target environment name (e.g., 'production')
            with_db: Also copy database content
            dry_run: Preview changes without applying

        Returns:
            PromoteResult with operation details

        Raises:
            EnvironmentServiceError: If promotion fails
        """
        # Validate source environment
        source = self.db.get_environment(project_name, source_env)
        if not source:
            raise EnvironmentServiceError(
                code="SOURCE_ENV_NOT_FOUND",
                message=f"Source environment '{source_env}' not found",
                suggestion=(
                    f"Run 'hostkit environment list {project_name}' to see available environments"
                ),
            )

        # Validate target environment
        target = self.db.get_environment(project_name, target_env)
        if not target:
            raise EnvironmentServiceError(
                code="TARGET_ENV_NOT_FOUND",
                message=f"Target environment '{target_env}' not found",
                suggestion=(
                    f"Run 'hostkit environment list {project_name}' to see available environments"
                ),
            )

        if dry_run:
            return PromoteResult(
                success=True,
                source_env=source_env,
                target_env=target_env,
                code_copied=True,
                db_copied=with_db and source.get("db_name") is not None,
                message="Dry run - no changes made",
            )

        source_user = source["linux_user"]
        target_user = target["linux_user"]

        try:
            # 1. Stop target environment
            self._stop_service(target_user)

            # 2. Copy code
            self._copy_code(source_user, target_user)

            # 3. Copy database if requested
            db_copied = False
            if with_db and source.get("db_name"):
                self._copy_database(
                    f"{project_name}_{source_env}_db",
                    f"{project_name}_{target_env}_db",
                )
                db_copied = True

            # 4. Restart target environment
            self._start_service(target_user)

            return PromoteResult(
                success=True,
                source_env=source_env,
                target_env=target_env,
                code_copied=True,
                db_copied=db_copied,
                message=f"Promoted {source_env} -> {target_env}",
            )

        except Exception as e:
            # Try to restart target
            try:
                self._start_service(target_user)
            except Exception:
                pass
            raise EnvironmentServiceError(
                code="PROMOTE_FAILED",
                message=f"Failed to promote: {e}",
                suggestion="Check system logs. Target environment may need manual recovery.",
            )

    def start_environment(self, project_name: str, env_name: str) -> None:
        """Start an environment's service."""
        env = self.get_environment(project_name, env_name)
        self._start_service(env.linux_user)
        self.db.update_environment_status(project_name, env_name, "running")

    def stop_environment(self, project_name: str, env_name: str) -> None:
        """Stop an environment's service."""
        env = self.get_environment(project_name, env_name)
        self._stop_service(env.linux_user)
        self.db.update_environment_status(project_name, env_name, "stopped")

    def restart_environment(self, project_name: str, env_name: str) -> None:
        """Restart an environment's service."""
        env = self.get_environment(project_name, env_name)
        self._restart_service(env.linux_user)
        self.db.update_environment_status(project_name, env_name, "running")

    # =========================================================================
    # Private helper methods
    # =========================================================================

    def _to_environment_info(self, env: dict[str, Any]) -> EnvironmentInfo:
        """Convert database record to EnvironmentInfo."""
        return EnvironmentInfo(
            id=env["id"],
            project_name=env["project_name"],
            env_name=env["env_name"],
            linux_user=env["linux_user"],
            port=env["port"],
            db_name=env.get("db_name"),
            share_parent_db=bool(env.get("share_parent_db", 0)),
            status=env["status"],
            created_at=env["created_at"],
            created_by=env["created_by"],
        )

    def _create_linux_user(self, name: str) -> None:
        """Create a Linux user for the environment."""
        subprocess.run(
            [
                "useradd",
                "--system",
                "--create-home",
                "--home-dir",
                f"/home/{name}",
                "--shell",
                "/bin/bash",
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
            pass

    def _create_environment_directories(self, name: str, runtime: str) -> None:
        """Create directory structure for environment."""
        home = Path(f"/home/{name}")

        # Create releases directory for deployment
        dirs = [home / "releases", home / "shared"]

        if runtime == "python":
            dirs.append(home / "venv")
        elif runtime in ("node", "nextjs"):
            dirs.append(home / "node_modules")

        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

        self._chown_recursive(home, name)

    def _create_env_file(
        self,
        linux_user: str,
        env_name: str,
        port: int,
        project_name: str,
        share_db: bool,
    ) -> None:
        """Create .env file for environment."""
        env_path = Path(f"/home/{linux_user}/.env")

        # If sharing parent db, copy connection from parent
        db_url = ""
        if share_db:
            parent_env = Path(f"/home/{project_name}/.env")
            if parent_env.exists():
                content = parent_env.read_text()
                for line in content.split("\n"):
                    if line.startswith("DATABASE_URL="):
                        db_url = line + "\n"
                        break

        content = f"""# Environment: {env_name}
# Parent project: {project_name}
PROJECT_NAME={linux_user}
ENVIRONMENT={env_name}
PORT={port}
HOST=127.0.0.1
{db_url}"""

        env_path.write_text(content)
        subprocess.run(["chown", f"{linux_user}:{linux_user}", str(env_path)], check=True)
        subprocess.run(["chmod", "600", str(env_path)], check=True)

    def _create_database(self, project_name: str, env_name: str, linux_user: str) -> str:
        """Create a database for the environment."""
        from hostkit.services.database_service import DatabaseService

        db_service = DatabaseService()

        # Use environment user as database name base

        # Create database with environment user as owner
        credentials = db_service.create_database(f"{project_name}_{env_name}")

        # Update .env file with database URL
        env_path = Path(f"/home/{linux_user}/.env")
        if env_path.exists():
            content = env_path.read_text()
            content += f"\nDATABASE_URL=postgresql://{credentials.username}:{credentials.password}@localhost/{credentials.database}\n"
            env_path.write_text(content)

        return credentials.database

    def _delete_database(self, project_name: str, env_name: str) -> None:
        """Delete environment's database."""
        try:
            from hostkit.services.database_service import DatabaseService

            db_service = DatabaseService()
            db_service.delete_database(f"{project_name}_{env_name}", force=True)
        except Exception:
            pass

    def _create_systemd_service(self, linux_user: str, runtime: str, port: int) -> None:
        """Create systemd service for environment."""
        from hostkit.services.project_service import (
            DEFAULT_START_COMMANDS,
            SYSTEMD_TEMPLATE,
        )

        start_command = DEFAULT_START_COMMANDS.get(runtime, DEFAULT_START_COMMANDS["python"])
        start_command = start_command.format(project_name=linux_user)

        service_content = SYSTEMD_TEMPLATE.format(
            project_name=linux_user,
            start_command=start_command,
            resource_limits="",
        )

        service_path = Path(f"/etc/systemd/system/hostkit-{linux_user}.service")
        service_path.write_text(service_content)

        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _remove_systemd_service(self, name: str) -> None:
        """Remove systemd service."""
        service_path = Path(f"/etc/systemd/system/hostkit-{name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _create_nginx_config(self, linux_user: str, domain: str, port: int) -> None:
        """Create Nginx configuration for environment."""
        from jinja2 import Template

        template = Template("""# Managed by HostKit Environment
# Environment: {{ linux_user }}
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
            linux_user=linux_user,
            domain=domain,
            port=port,
            timestamp=datetime.utcnow().isoformat(),
        )

        config_path = Path(f"/etc/nginx/sites-available/{linux_user}")
        config_path.write_text(config)

        # Enable site
        enabled_path = Path(f"/etc/nginx/sites-enabled/{linux_user}")
        if enabled_path.exists():
            enabled_path.unlink()
        enabled_path.symlink_to(config_path)

        # Reload nginx
        subprocess.run(["nginx", "-t"], check=True, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)

    def _remove_nginx_config(self, name: str) -> None:
        """Remove Nginx configuration."""
        config_path = Path(f"/etc/nginx/sites-available/{name}")
        enabled_path = Path(f"/etc/nginx/sites-enabled/{name}")

        if enabled_path.exists():
            enabled_path.unlink()
        if config_path.exists():
            config_path.unlink()

        try:
            subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass

    def _create_log_directory(self, name: str) -> None:
        """Create log directory for environment."""
        log_dir = Path(f"/var/log/projects/{name}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "app.log").touch()
        (log_dir / "error.log").touch()
        self._chown_recursive(log_dir, name)

    def _remove_log_directory(self, name: str) -> None:
        """Remove log directory."""
        import shutil

        log_dir = Path(f"/var/log/projects/{name}")
        if log_dir.exists():
            shutil.rmtree(log_dir)

    def _create_sudoers(self, linux_user: str, project_name: str, env_name: str) -> None:
        """Create sudoers file for environment user."""
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-{linux_user}")

        # Environment users can run hostkit commands for their environment
        content = f"""# HostKit environment sudoers for {linux_user}
# Generated by HostKit
{linux_user} ALL=(root) NOPASSWD: /usr/local/bin/hostkit deploy {project_name} --env {env_name} *
{linux_user} ALL=(root) NOPASSWD: /usr/local/bin/hostkit health {project_name} --env {env_name} *
{linux_user} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service * {linux_user}
{linux_user} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log * {linux_user}
{linux_user} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env * {project_name} --env {env_name} *
"""
        sudoers_path.write_text(content)
        subprocess.run(["chmod", "440", str(sudoers_path)], check=True)

    def _remove_sudoers(self, linux_user: str) -> None:
        """Remove sudoers file."""
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-{linux_user}")
        if sudoers_path.exists():
            sudoers_path.unlink()

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

    def _restart_service(self, name: str) -> None:
        """Restart systemd service."""
        subprocess.run(
            ["systemctl", "restart", f"hostkit-{name}"],
            check=True,
            capture_output=True,
        )

    def _chown_recursive(self, path: Path, user: str) -> None:
        """Recursively change ownership."""
        subprocess.run(
            ["chown", "-R", f"{user}:{user}", str(path)],
            check=True,
            capture_output=True,
        )

    def _copy_code(self, source_user: str, target_user: str) -> None:
        """Copy code from source to target environment."""
        source_app = Path(f"/home/{source_user}/app")
        target_app = Path(f"/home/{target_user}/app")

        if source_app.exists():
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--delete",
                    f"{source_app}/",
                    f"{target_app}/",
                ],
                check=True,
                capture_output=True,
            )
            self._chown_recursive(target_app, target_user)

        # Also copy venv or node_modules
        for subdir in ["venv", "node_modules"]:
            source_dir = Path(f"/home/{source_user}/{subdir}")
            target_dir = Path(f"/home/{target_user}/{subdir}")
            if source_dir.exists():
                subprocess.run(
                    [
                        "rsync",
                        "-a",
                        "--delete",
                        f"{source_dir}/",
                        f"{target_dir}/",
                    ],
                    check=True,
                    capture_output=True,
                )
                self._chown_recursive(target_dir, target_user)

    def _copy_database(self, source_db: str, target_db: str) -> None:
        """Copy database content from source to target."""
        admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        env = os.environ.copy()
        if admin_password:
            env["PGPASSWORD"] = admin_password

        # pg_dump | psql pipeline
        pg_dump_cmd = [
            "pg_dump",
            "-h",
            "localhost",
            "-U",
            admin_user,
            "-d",
            source_db,
            "--no-owner",
            "--no-acl",
            "--clean",
        ]

        psql_cmd = [
            "psql",
            "-h",
            "localhost",
            "-U",
            admin_user,
            "-d",
            target_db,
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

    def _cleanup_environment_resources(
        self, linux_user: str, project_name: str, env_name: str
    ) -> None:
        """Clean up all resources for an environment."""
        # Stop service
        self._stop_service(linux_user)

        # Remove systemd service
        self._remove_systemd_service(linux_user)

        # Remove nginx config
        self._remove_nginx_config(linux_user)

        # Remove sudoers
        self._remove_sudoers(linux_user)

        # Delete Linux user and home directory
        self._delete_linux_user(linux_user)

        # Remove log directory
        self._remove_log_directory(linux_user)

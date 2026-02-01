"""Project management service for HostKit."""

import os
import pwd
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db

# Valid project name pattern: lowercase alphanumeric with hyphens, must start with letter,
# end with letter/number (not hyphen), 3-32 chars
PROJECT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")

# Supported runtimes
SUPPORTED_RUNTIMES = ("python", "node", "nextjs", "static")

# Systemd service template
SYSTEMD_TEMPLATE = """[Unit]
Description=HostKit Project: {project_name}
After=network.target

[Service]
Type=simple
User={project_name}
Group={project_name}
WorkingDirectory={working_directory}
EnvironmentFile=/home/{project_name}/.env
ExecStart={start_command}
Restart=always
RestartSec=5
StandardOutput=append:/var/log/projects/{project_name}/app.log
StandardError=append:/var/log/projects/{project_name}/error.log
{resource_limits}
[Install]
WantedBy=multi-user.target
"""

# Default working directories per runtime
# Python needs /home/{project_name} so `python -m app` can find the app/ module
# Other runtimes use /home/{project_name}/app as their working directory
DEFAULT_WORKING_DIRECTORIES = {
    "python": "/home/{project_name}",
    "node": "/home/{project_name}/app",
    "nextjs": "/home/{project_name}/app",
    "static": "/home/{project_name}/app",
}

# Default start commands per runtime
DEFAULT_START_COMMANDS = {
    "python": "/home/{project_name}/venv/bin/python -m app",
    "node": "/usr/bin/node /home/{project_name}/app/index.js",
    "nextjs": "/usr/bin/npm start",  # Runs in WorkingDirectory (app dir)
    "static": "/bin/true",  # Static sites don't need a process
}


@dataclass
class ProjectInfo:
    """Project information structure."""

    name: str
    runtime: str
    port: int
    redis_db: int | None
    status: str
    created_at: str
    description: str | None = None


class ProjectServiceError(Exception):
    """Base exception for project service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class ProjectService:
    """Service for managing HostKit projects."""

    def __init__(self) -> None:
        self.db = get_db()
        self.config = get_config()

    def validate_project_name(self, name: str) -> None:
        """Validate project name format and availability."""
        if not PROJECT_NAME_PATTERN.match(name):
            raise ProjectServiceError(
                code="INVALID_PROJECT_NAME",
                message=f"Invalid project name '{name}'",
                suggestion=(
                    "Project names must be 3-32 characters,"
                    " lowercase letters/numbers/hyphens,"
                    " start with a letter, end with"
                    " letter or number (not hyphen)"
                ),
            )

        # Check if project already exists
        if self.db.get_project(name):
            raise ProjectServiceError(
                code="PROJECT_EXISTS",
                message=f"Project '{name}' already exists",
                suggestion="Choose a different name or delete the existing project first",
            )

        # Check if Linux user already exists
        try:
            pwd.getpwnam(name)
            raise ProjectServiceError(
                code="USER_EXISTS",
                message=f"Linux user '{name}' already exists",
                suggestion="Choose a different project name",
            )
        except KeyError:
            pass  # User doesn't exist, which is what we want

    def validate_runtime(self, runtime: str) -> None:
        """Validate runtime type."""
        if runtime not in SUPPORTED_RUNTIMES:
            raise ProjectServiceError(
                code="INVALID_RUNTIME",
                message=f"Unsupported runtime '{runtime}'",
                suggestion=f"Supported runtimes: {', '.join(SUPPORTED_RUNTIMES)}",
            )

    def create_project(
        self,
        name: str,
        runtime: str = "python",
        description: str | None = None,
        create_storage: bool = False,
        start_cmd: str | None = None,
    ) -> ProjectInfo:
        """Create a new project with all system resources."""
        # Validate inputs
        self.validate_project_name(name)
        self.validate_runtime(runtime)

        try:
            # 1. Create Linux user with home directory
            self._create_linux_user(name)

            # 2. Create directory structure
            self._create_project_directories(name, runtime)

            # 3. Assign port and redis db
            port = self.db.get_next_port()
            redis_db = self.db.get_next_redis_db()

            # 4. Create .env file
            self._create_env_file(name, port, redis_db, runtime)

            # 5. Generate systemd service
            self._create_systemd_service(name, runtime, port, start_cmd)

            # 6. Create log directory
            self._create_log_directory(name)

            # 7. Create sudoers rules for user-level access
            self._create_sudoers_rules(name)

            # 8. Register in database (must happen before SSH key audit)
            # Get current user as the creator
            created_by = os.environ.get("USER", "root")
            # If running via sudo, get the actual operator username
            if created_by == "root":
                sudo_user = os.environ.get("SUDO_USER")
                if sudo_user:
                    created_by = sudo_user

            project_data = self.db.create_project(
                name=name,
                runtime=runtime,
                port=port,
                redis_db=redis_db,
                description=description,
                created_by=created_by,
            )

            # 9. Add operator SSH keys (for Claude Code agents to access)
            self._add_operator_ssh_keys(name)

            # 10. Auto-register hostkit.dev subdomain
            hostkit_domain = f"{name}.hostkit.dev"
            try:
                self.db.add_domain(name, hostkit_domain, ssl_provisioned=True)
            except Exception:
                pass  # Domain might already exist

            # 11. Update nginx port mappings for wildcard routing
            self._regenerate_nginx_port_mappings()

            # 12. Create storage bucket if requested
            if create_storage:
                try:
                    from hostkit.services.storage_service import StorageService

                    storage = StorageService()
                    if storage.is_minio_running():
                        storage.create_bucket(f"{name}-storage", name)
                except Exception:
                    pass  # Storage is optional, don't fail project creation

            return ProjectInfo(
                name=project_data["name"],
                runtime=project_data["runtime"],
                port=project_data["port"],
                redis_db=project_data["redis_db"],
                status=project_data["status"],
                created_at=project_data["created_at"],
                description=project_data.get("description"),
            )

        except ProjectServiceError:
            # Re-raise our own errors
            raise
        except Exception as e:
            # Clean up on failure
            self._cleanup_failed_project(name)
            raise ProjectServiceError(
                code="PROJECT_CREATE_FAILED",
                message=f"Failed to create project: {e}",
                suggestion="Check system logs for details",
            )

    def delete_project(self, name: str, force: bool = False) -> None:
        """Delete a project and all its resources."""
        # Check project exists
        project = self.db.get_project(name)
        if not project:
            raise ProjectServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        if not force:
            raise ProjectServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a project requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        # 1. Stop service if running
        self._stop_systemd_service(name)

        # 2. Remove systemd service
        self._remove_systemd_service(name)

        # 3. Remove sudoers rules
        self._remove_sudoers_rules(name)

        # 4. Delete Linux user and home directory
        self._delete_linux_user(name)

        # 5. Clean up log directory
        self._remove_log_directory(name)

        # 6. Clean up storage bucket if exists
        try:
            from hostkit.services.storage_service import StorageService

            storage = StorageService()
            if storage.is_minio_running():
                storage.cleanup_project_bucket(name)
        except Exception:
            pass  # Best effort cleanup

        # 7. Remove from database (cascades to domains and backups)
        self.db.delete_project(name)

    def list_projects(self) -> list[ProjectInfo]:
        """List all projects."""
        projects = self.db.list_projects()
        result = []

        for p in projects:
            # Get actual service status
            service_status = self._get_service_status(p["name"])

            result.append(
                ProjectInfo(
                    name=p["name"],
                    runtime=p["runtime"],
                    port=p["port"],
                    redis_db=p["redis_db"],
                    status=service_status,
                    created_at=p["created_at"],
                    description=p.get("description"),
                )
            )

        return result

    def get_project(self, name: str) -> ProjectInfo:
        """Get detailed information about a project."""
        project = self.db.get_project(name)
        if not project:
            raise ProjectServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # Get actual service status
        service_status = self._get_service_status(name)

        return ProjectInfo(
            name=project["name"],
            runtime=project["runtime"],
            port=project["port"],
            redis_db=project["redis_db"],
            status=service_status,
            created_at=project["created_at"],
            description=project.get("description"),
        )

    def get_project_details(self, name: str) -> dict[str, Any]:
        """Get extended project details including domains and resource usage."""
        project = self.get_project(name)
        domains = self.db.list_domains(name)
        backups = self.db.list_backups(name)

        # Get home directory size
        home_size = self._get_directory_size(f"/home/{name}")

        # Get log size
        log_size = self._get_directory_size(f"/var/log/projects/{name}")

        return {
            "project": {
                "name": project.name,
                "runtime": project.runtime,
                "port": project.port,
                "redis_db": project.redis_db,
                "status": project.status,
                "created_at": project.created_at,
                "description": project.description,
            },
            "service": {
                "name": f"hostkit-{name}",
                "status": project.status,
            },
            "paths": {
                "home": f"/home/{name}",
                "app": f"/home/{name}/app",
                "logs": f"/var/log/projects/{name}",
                "env": f"/home/{name}/.env",
            },
            "resources": {
                "home_size": home_size,
                "log_size": log_size,
            },
            "domains": [
                {"domain": d["domain"], "ssl": bool(d["ssl_provisioned"])} for d in domains
            ],
            "backups": [
                {"id": b["id"], "type": b["type"], "created_at": b["created_at"]}
                for b in backups[:5]  # Last 5 backups
            ],
        }

    def get_project_enabled_services(self, name: str) -> list[str]:
        """Get list of enabled services for a project.

        Checks each service type to see if it's enabled for the given project.

        Args:
            name: Project name

        Returns:
            List of enabled service names, e.g. ['db', 'redis', 'auth']
        """
        project = self.db.get_project(name)
        if not project:
            return []

        services: list[str] = []

        # Check database (PostgreSQL)
        try:
            from hostkit.services.database_service import DatabaseService

            db_service = DatabaseService()
            if db_service.database_exists(name):
                services.append("db")
        except Exception:
            pass

        # Check redis (from project record)
        if project.get("redis_db") is not None:
            services.append("redis")

        # Check auth service
        try:
            from hostkit.services.auth_service import AuthService

            auth_service = AuthService()
            if auth_service.auth_is_enabled(name):
                services.append("auth")
        except Exception:
            pass

        # Check payments service
        try:
            from hostkit.services.payment_service import PaymentService

            payment_service = PaymentService()
            if payment_service.payment_is_enabled(name):
                services.append("payments")
        except Exception:
            pass

        # Check SMS service
        try:
            from hostkit.services.sms_service import SMSService

            sms_service = SMSService()
            if sms_service.sms_is_enabled(name):
                services.append("sms")
        except Exception:
            pass

        # Check voice service
        try:
            from hostkit.services.voice_service import VoiceService

            voice_service = VoiceService()
            if voice_service.voice_is_enabled(name):
                services.append("voice")
        except Exception:
            pass

        # Check booking service
        try:
            from hostkit.services.booking_service import BookingService

            booking_service = BookingService()
            if booking_service.booking_is_enabled(name):
                services.append("booking")
        except Exception:
            pass

        # Check R2 storage
        try:
            from hostkit.services.r2_service import R2Service

            r2_service = R2Service()
            if r2_service.is_enabled(name):
                services.append("r2")
        except Exception:
            pass

        # Check mail service
        try:
            from hostkit.services.mail_service import MailService

            mail_service = MailService()
            if mail_service.is_project_mail_enabled(name):
                services.append("mail")
        except Exception:
            pass

        # Check MinIO storage
        try:
            from hostkit.services.storage_service import StorageService

            storage_service = StorageService()
            if storage_service.storage_is_enabled(name):
                services.append("minio")
        except Exception:
            pass

        # Check vector service (uses separate vector.db)
        try:
            import sqlite3

            vector_db_path = self.config.data_dir / "vector.db"
            if vector_db_path.exists():
                conn = sqlite3.connect(vector_db_path)
                cursor = conn.execute(
                    "SELECT id FROM vector_projects WHERE project_name = ?",
                    (name,),
                )
                if cursor.fetchone():
                    services.append("vector")
                conn.close()
        except Exception:
            pass

        return services

    def regenerate_sudoers(self, name: str) -> dict[str, Any]:
        """Regenerate sudoers rules for an existing project.

        This updates the project's sudoers file with the latest rules,
        enabling project-scoped access to hostkit commands.

        Args:
            name: Project name

        Returns:
            dict with regeneration status

        Raises:
            ProjectServiceError: If project doesn't exist
        """
        # Verify project exists
        self.get_project(name)

        # Regenerate sudoers rules
        self._create_sudoers_rules(name)

        return {
            "name": name,
            "sudoers_file": f"/etc/sudoers.d/hostkit-{name}",
            "status": "regenerated",
        }

    def regenerate_all_sudoers(self) -> list[dict[str, Any]]:
        """Regenerate sudoers rules for all existing projects.

        Returns:
            list of dicts with regeneration status for each project
        """
        results = []
        projects = self.list_projects()

        for project in projects:
            try:
                result = self.regenerate_sudoers(project.name)
                result["success"] = True
                results.append(result)
            except Exception as e:
                results.append(
                    {
                        "name": project.name,
                        "success": False,
                        "error": str(e),
                    }
                )

        return results

    # Private helper methods

    def _regenerate_nginx_port_mappings(self) -> None:
        """Regenerate nginx port mapping files for wildcard routing.

        Creates port mapping files for the *.hostkit.dev wildcard server block:
        - /etc/nginx/hostkit-ports.conf (main project ports)
        - /etc/nginx/hostkit-auth-ports.conf (auth service ports)
        - /etc/nginx/hostkit-payment-ports.conf (payment service ports)
        - /etc/nginx/hostkit-sms-ports.conf (SMS service ports)
        - /etc/nginx/hostkit-booking-ports.conf (booking service ports)
        - /etc/nginx/hostkit-chatbot-ports.conf (chatbot service ports)
        """
        from pathlib import Path

        # Get all projects with ports
        projects = self.db.list_projects()

        # Generate project port mappings
        port_lines = ["# Auto-generated project port mappings"]
        for p in projects:
            if p.get("port"):
                port_lines.append(
                    f'if ($project = "{p["name"]}") {{ set $project_port {p["port"]}; }}'
                )

        port_conf = Path("/etc/nginx/hostkit-ports.conf")
        port_conf.write_text("\n".join(port_lines) + "\n")

        # Generate auth port mappings
        auth_lines = ["# Auto-generated auth service port mappings"]
        for p in projects:
            auth_record = self.db.get_auth_service(p["name"])
            if auth_record and auth_record.get("auth_port"):
                auth_lines.append(
                    f'if ($project = "{p["name"]}")'
                    f" {{ set $auth_port {auth_record['auth_port']}; }}"
                )

        auth_conf = Path("/etc/nginx/hostkit-auth-ports.conf")
        auth_conf.write_text("\n".join(auth_lines) + "\n")

        # Generate payment port mappings
        payment_lines = ["# Auto-generated payment service port mappings"]
        for p in projects:
            project_port = p.get("port")
            if project_port:
                # Check if payment service is enabled for this project
                from hostkit.services.payment_service import PaymentService

                try:
                    payment_service = PaymentService()
                    if payment_service.payment_is_enabled(p["name"]):
                        payment_port = project_port + 2000
                        payment_lines.append(
                            f'if ($project = "{p["name"]}") {{ set $payment_port {payment_port}; }}'
                        )
                except Exception:
                    pass  # Skip if payment service check fails

        payment_conf = Path("/etc/nginx/hostkit-payment-ports.conf")
        payment_conf.write_text("\n".join(payment_lines) + "\n")

        # Generate SMS port mappings
        sms_lines = ["# Auto-generated SMS service port mappings"]
        for p in projects:
            project_port = p.get("port")
            if project_port:
                # Check if SMS service is enabled for this project
                from hostkit.services.sms_service import SMSService

                try:
                    sms_service = SMSService()
                    if sms_service.sms_is_enabled(p["name"]):
                        sms_port = project_port + 3000
                        sms_lines.append(
                            f'if ($project = "{p["name"]}") {{ set $sms_port {sms_port}; }}'
                        )
                except Exception:
                    pass  # Skip if SMS service check fails

        sms_conf = Path("/etc/nginx/hostkit-sms-ports.conf")
        sms_conf.write_text("\n".join(sms_lines) + "\n")

        # Generate booking port mappings
        booking_lines = ["# Auto-generated booking service port mappings"]
        for p in projects:
            project_port = p.get("port")
            if project_port:
                # Check if booking service is enabled for this project
                from hostkit.services.booking_service import BookingService

                try:
                    booking_service = BookingService()
                    if booking_service.booking_is_enabled(p["name"]):
                        booking_port = project_port + 4000
                        booking_lines.append(
                            f'if ($project = "{p["name"]}") {{ set $booking_port {booking_port}; }}'
                        )
                except Exception:
                    pass  # Skip if booking service check fails

        booking_conf = Path("/etc/nginx/hostkit-booking-ports.conf")
        booking_conf.write_text("\n".join(booking_lines) + "\n")

        # Generate chatbot port mappings
        chatbot_lines = [
            "# Auto-generated chatbot port mappings",
            '# Format: if ($project = "name") { set $chatbot_port PORT; }',
        ]
        for p in projects:
            project_port = p.get("port")
            if project_port:
                # Check if chatbot service is enabled for this project
                from hostkit.services.chatbot_service import ChatbotService

                try:
                    chatbot_service = ChatbotService()
                    if chatbot_service.chatbot_is_enabled(p["name"]):
                        chatbot_port = project_port + 5000
                        chatbot_lines.append(
                            f'if ($project = "{p["name"]}") {{ set $chatbot_port {chatbot_port}; }}'
                        )
                except Exception:
                    pass  # Skip if chatbot service check fails

        chatbot_conf = Path("/etc/nginx/hostkit-chatbot-ports.conf")
        chatbot_conf.write_text("\n".join(chatbot_lines) + "\n")

        # Reload nginx if running
        try:
            subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass  # nginx might not be running

    def _create_linux_user(self, name: str) -> None:
        """Create a Linux user for the project."""
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
            # User might not exist, that's okay
            pass

    def _create_project_directories(self, name: str, runtime: str) -> None:
        """Create the project directory structure."""
        home = Path(f"/home/{name}")

        dirs = [
            home / "app",
            home / "logs",
        ]

        # Runtime-specific directories
        if runtime == "python":
            dirs.append(home / "venv")
        elif runtime == "node":
            dirs.append(home / "node_modules")
        elif runtime == "nextjs":
            # Next.js uses npm, so node_modules will be in the app directory
            # No additional directories needed at home level
            pass

        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

        # Set ownership
        self._chown_recursive(home, name, name)

    def _create_env_file(self, name: str, port: int, redis_db: int | None, runtime: str) -> None:
        """Create the project's .env file."""
        db_num = redis_db if redis_db is not None else 0
        env_content = f"""# HostKit Project Environment
PROJECT_NAME={name}
PORT={port}
HOST=127.0.0.1

# Redis (assigned database)
REDIS_URL=redis://localhost:6379/{db_num}

# Celery (uses Redis as broker)
CELERY_BROKER_URL=redis://localhost:6379/{db_num}
CELERY_RESULT_BACKEND=redis://localhost:6379/{db_num}

# PostgreSQL (will be configured when database is created)
# DATABASE_URL=postgresql://...
"""
        # Add runtime-specific environment variables
        if runtime == "nextjs":
            env_content += """
# Next.js specific
NODE_ENV=production
"""

        env_content += """
# Add your application-specific variables below
"""
        env_path = Path(f"/home/{name}/.env")
        env_path.write_text(env_content)

        # Set ownership and permissions
        subprocess.run(["chown", f"{name}:{name}", str(env_path)], check=True)
        subprocess.run(["chmod", "600", str(env_path)], check=True)

    def _create_systemd_service(
        self,
        name: str,
        runtime: str,
        port: int,
        custom_start_cmd: str | None = None,
        resource_limits: dict[str, str] | None = None,
    ) -> None:
        """Create a systemd service file for the project.

        Args:
            name: Project name
            runtime: Runtime type (python, node, nextjs, static)
            port: Project port
            custom_start_cmd: Custom start command (optional)
            resource_limits: Dict of systemd directives (e.g., {"CPUQuota": "100%"})
        """
        if custom_start_cmd:
            # Use custom start command if provided
            start_command = custom_start_cmd.format(project_name=name)
        else:
            start_command = DEFAULT_START_COMMANDS.get(runtime, DEFAULT_START_COMMANDS["python"])
            start_command = start_command.format(project_name=name)

        # Get working directory for runtime
        working_directory = DEFAULT_WORKING_DIRECTORIES.get(
            runtime, DEFAULT_WORKING_DIRECTORIES["python"]
        )
        working_directory = working_directory.format(project_name=name)

        # Build resource limits section
        limits_section = ""
        if resource_limits:
            limits_lines = ["# Resource Limits (managed by HostKit)"]
            for key, value in resource_limits.items():
                limits_lines.append(f"{key}={value}")
            limits_section = "\n".join(limits_lines) + "\n"

        service_content = SYSTEMD_TEMPLATE.format(
            project_name=name,
            start_command=start_command,
            working_directory=working_directory,
            resource_limits=limits_section,
        )

        service_path = Path(f"/etc/systemd/system/hostkit-{name}.service")
        service_path.write_text(service_content)

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _remove_systemd_service(self, name: str) -> None:
        """Remove a project's systemd service file."""
        service_path = Path(f"/etc/systemd/system/hostkit-{name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)

    def _stop_systemd_service(self, name: str) -> None:
        """Stop a project's systemd service."""
        try:
            subprocess.run(
                ["systemctl", "stop", f"hostkit-{name}"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # Service might not be running or not exist
            pass

    def _get_service_status(self, name: str) -> str:
        """Get the systemd service status for a project."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"hostkit-{name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = result.stdout.strip()
            # Map systemd status to our status
            if status == "active":
                return "running"
            elif status == "inactive":
                return "stopped"
            elif status == "failed":
                return "failed"
            else:
                return "stopped"
        except (subprocess.SubprocessError, FileNotFoundError):
            return "stopped"

    def _create_log_directory(self, name: str) -> None:
        """Create the centralized log directory for a project."""
        log_dir = Path(f"/var/log/projects/{name}")
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create initial log files
        (log_dir / "app.log").touch()
        (log_dir / "error.log").touch()

        # Set ownership
        self._chown_recursive(log_dir, name, name)

    def _remove_log_directory(self, name: str) -> None:
        """Remove a project's log directory."""
        import shutil

        log_dir = Path(f"/var/log/projects/{name}")
        if log_dir.exists():
            shutil.rmtree(log_dir)

    def _chown_recursive(self, path: Path, user: str, group: str) -> None:
        """Recursively change ownership of a directory."""
        subprocess.run(
            ["chown", "-R", f"{user}:{group}", str(path)],
            check=True,
            capture_output=True,
        )

    def _get_directory_size(self, path: str) -> str:
        """Get human-readable size of a directory."""
        try:
            result = subprocess.run(
                ["du", "-sh", path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.split()[0]
        except (subprocess.SubprocessError, IndexError):
            pass
        return "0"

    def _cleanup_failed_project(self, name: str) -> None:
        """Clean up resources after a failed project creation."""
        try:
            self._stop_systemd_service(name)
            self._remove_systemd_service(name)
            self._remove_sudoers_rules(name)
            self._delete_linux_user(name)
            self._remove_log_directory(name)
            self.db.delete_project(name)
        except Exception:
            pass  # Best effort cleanup

    def _add_operator_ssh_keys(self, name: str) -> None:
        """Add operator SSH keys to a new project for agent access.

        Reads operator_ssh_keys from config and adds each to the project's
        authorized_keys file. This enables Claude Code agents to SSH in.
        """
        from hostkit.services import ssh_service

        operator_keys = self.config.operator_ssh_keys
        if not operator_keys:
            return

        # Ensure .ssh directory exists
        ssh_service.ensure_ssh_dir(name)

        for key in operator_keys:
            try:
                ssh_service.add_key(name, key)
            except ValueError:
                # Key invalid or already exists - continue with others
                pass

    def _create_sudoers_rules(self, name: str) -> None:
        """Create sudoers rules to allow project user to manage their services."""
        from jinja2 import Template

        # Load template from templates directory
        template_path = self.config.templates_dir / "sudoers.j2"

        if not template_path.exists():
            # If template doesn't exist, generate inline
            sudoers_content = self._generate_sudoers_content(name)
        else:
            template_content = template_path.read_text()
            template = Template(template_content)
            sudoers_content = template.render(project_name=name)

        # Write to sudoers.d directory
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-{name}")
        sudoers_path.write_text(sudoers_content)

        # Set correct permissions (must be 0440 or 0400)
        subprocess.run(["chmod", "0440", str(sudoers_path)], check=True)

        # Validate the sudoers file
        result = subprocess.run(
            ["visudo", "-c", "-f", str(sudoers_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Invalid sudoers file - remove it and log warning
            sudoers_path.unlink()
            # Don't fail project creation, just skip sudoers

    def _generate_sudoers_content(self, name: str) -> str:
        """Generate sudoers content for a project user."""
        return f"""# Sudoers rules for HostKit project: {name}
# Generated by HostKit - DO NOT EDIT MANUALLY

# =============================================================================
# Service management (systemd)
# =============================================================================

# Main app service
{name} ALL=(root) NOPASSWD: /bin/systemctl start hostkit-{name}.service
{name} ALL=(root) NOPASSWD: /bin/systemctl stop hostkit-{name}.service
{name} ALL=(root) NOPASSWD: /bin/systemctl restart hostkit-{name}.service
{name} ALL=(root) NOPASSWD: /bin/systemctl enable hostkit-{name}.service
{name} ALL=(root) NOPASSWD: /bin/systemctl disable hostkit-{name}.service
{name} ALL=(root) NOPASSWD: /bin/systemctl status hostkit-{name}.service

# Auth service (if enabled)
{name} ALL=(root) NOPASSWD: /bin/systemctl start hostkit-{name}-auth.service
{name} ALL=(root) NOPASSWD: /bin/systemctl stop hostkit-{name}-auth.service
{name} ALL=(root) NOPASSWD: /bin/systemctl restart hostkit-{name}-auth.service
{name} ALL=(root) NOPASSWD: /bin/systemctl enable hostkit-{name}-auth.service
{name} ALL=(root) NOPASSWD: /bin/systemctl disable hostkit-{name}-auth.service
{name} ALL=(root) NOPASSWD: /bin/systemctl status hostkit-{name}-auth.service

# Worker service (if created)
{name} ALL=(root) NOPASSWD: /bin/systemctl start hostkit-{name}-worker.service
{name} ALL=(root) NOPASSWD: /bin/systemctl stop hostkit-{name}-worker.service
{name} ALL=(root) NOPASSWD: /bin/systemctl restart hostkit-{name}-worker.service
{name} ALL=(root) NOPASSWD: /bin/systemctl enable hostkit-{name}-worker.service
{name} ALL=(root) NOPASSWD: /bin/systemctl disable hostkit-{name}-worker.service
{name} ALL=(root) NOPASSWD: /bin/systemctl status hostkit-{name}-worker.service

# Journalctl access for logs
{name} ALL=(root) NOPASSWD: /bin/journalctl -u hostkit-{name}.service *
{name} ALL=(root) NOPASSWD: /bin/journalctl -u hostkit-{name}-auth.service *
{name} ALL=(root) NOPASSWD: /bin/journalctl -u hostkit-{name}-worker.service *

# =============================================================================
# HostKit commands (project-scoped)
# =============================================================================

# Deploy and rollback
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit deploy {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit deploy {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit rollback {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit rollback {name} *

# Database operations
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db backup {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db backup {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db restore {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db shell {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db info {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit db migrate {name} *

# Environment variables
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env list {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env list {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env get {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env set {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env unset {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env import {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit env sync {name} *

# Migrations
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit migrate {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit migrate {name} *

# Health and status (read-only)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit health {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit health {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit diagnose {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit diagnose {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit status {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit project info {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit capabilities --project {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit capabilities --project {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit --json capabilities --project {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit --json capabilities --project {name} *

# Resource limits
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits show {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits set {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits set {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits reset {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits reset {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits apply {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit limits disk {name}

# Service management via hostkit
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service start {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service stop {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service restart {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service status {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service logs {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit service logs {name} *

# Backup operations
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit backup create {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit backup create {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit backup list {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit backup restore {name} *

# Log operations
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log show {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log show {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log search {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log stats {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit log files {name}

# Secrets portal (generate magic link for web UI)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets portal {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets portal {name} *

# Secrets definition (define what secrets are needed)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets define {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets define {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets undefine {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets clear {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets clear {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets list {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit secrets verify {name}

# =============================================================================
# Domain and SSL management (project-scoped)
# =============================================================================

# SSH key management (add team members)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssh add-key {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssh add-key {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssh remove-key {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssh list-keys {name}

# Nginx domain management (their domain, their project)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit nginx add {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit nginx remove {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit nginx list {name}

# SSL provisioning (for domains belonging to this project)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssl provision *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssl status *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit ssl renew *

# =============================================================================
# Authentication service management (isolated per-project)
# =============================================================================

# Auth enable/disable (their isolated auth instance)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth enable {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth enable {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth disable {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth disable {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth status {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth config {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth config {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth users {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth users {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth logs {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth logs {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth export-key {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit auth export-key {name} *

# =============================================================================
# Mail service management (project-scoped)
# =============================================================================

# Mail enable/disable (their isolated mail subdomain)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail enable {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail disable {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail disable {name} *

# Mailbox management (add/remove mailboxes for their domain)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail add {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail remove {name} *
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail list {name}

# Credentials (view/reset passwords for their mailboxes)
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail credentials {name}
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail credentials {name} *

# Send test email
{name} ALL=(root) NOPASSWD: /usr/local/bin/hostkit mail send-test {name} *
"""

    def _remove_sudoers_rules(self, name: str) -> None:
        """Remove sudoers rules for a project."""
        sudoers_path = Path(f"/etc/sudoers.d/hostkit-{name}")
        if sudoers_path.exists():
            sudoers_path.unlink()

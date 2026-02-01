"""Chatbot service management for HostKit.

Provides per-project AI-powered chatbot with embeddable widget,
conversation history, and SSE streaming responses.
"""

import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register chatbot service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="chatbot",
        description="AI-powered chatbot with embeddable widget and SSE streaming",
        provision_flag="--with-chatbot",
        enable_command="hostkit chatbot enable {project}",
        env_vars_provided=["CHATBOT_URL", "CHATBOT_API_KEY"],
        related_commands=[
            "chatbot enable",
            "chatbot disable",
            "chatbot status",
            "chatbot config",
            "chatbot stats",
            "chatbot logs",
        ],
    )
)


@dataclass
class ChatbotDatabaseCredentials:
    """Chatbot database connection credentials."""

    database: str
    username: str
    password: str
    host: str
    port: int

    @property
    def connection_url(self) -> str:
        """Generate PostgreSQL connection URL."""
        from urllib.parse import quote_plus

        encoded_password = quote_plus(self.password)
        return f"postgresql://{self.username}:{encoded_password}@{self.host}:{self.port}/{self.database}"


class ChatbotServiceError(Exception):
    """Base exception for chatbot service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure password."""
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key(project: str) -> str:
    """Generate an API key for the chatbot service."""
    random_part = secrets.token_urlsafe(24)
    return f"ck_{project}_{random_part}"


class ChatbotService:
    """Service for managing per-project chatbot services."""

    def __init__(self) -> None:
        """Initialize the chatbot service."""
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        # Load LLM credentials from /etc/hostkit/llm.ini
        self._llm_config = self._load_llm_credentials()

    def _load_llm_credentials(self) -> dict[str, str]:
        """Load LLM credentials from /etc/hostkit/llm.ini."""
        llm_ini_path = Path("/etc/hostkit/llm.ini")
        if not llm_ini_path.exists():
            return {}  # Return empty, will error on enable if not configured

        try:
            import configparser

            config = configparser.ConfigParser()
            config.read(llm_ini_path)

            result = {}
            if config.has_section("anthropic"):
                result["anthropic_api_key"] = config.get("anthropic", "api_key", fallback="")
            if config.has_section("openai"):
                result["openai_api_key"] = config.get("openai", "api_key", fallback="")
            if config.has_section("defaults"):
                result["default_provider"] = config.get(
                    "defaults", "provider", fallback="anthropic"
                )
                result["default_model"] = config.get(
                    "defaults",
                    "model",
                    fallback="claude-sonnet-4-20250514",
                )
            return result
        except Exception:
            return {}

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
            raise ChatbotServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL: {e}",
                suggestion="Check PostgreSQL is running and credentials are correct",
            )

    def _chatbot_db_name(self, project: str) -> str:
        """Generate chatbot database name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_chatbot_db"

    def _chatbot_role_name(self, project: str) -> str:
        """Generate chatbot database role name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_chatbot_user"

    def _chatbot_port(self, project: str) -> int:
        """Calculate chatbot service port from project port.

        Chatbot service runs on project_port + 5000.
        (Auth uses +1000, payment uses +2000, SMS uses +3000,
        booking uses +4000, chatbot uses +5000)
        """
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise ChatbotServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return project_data["port"] + 5000

    def _chatbot_dir(self, project: str) -> Path:
        """Get the chatbot service directory for a project."""
        return Path(f"/home/{project}/.chatbot")

    def _validate_identifier(self, name: str) -> None:
        """Validate a PostgreSQL identifier to prevent SQL injection.

        Accepts project names with hyphens (they get converted to underscores
        for PostgreSQL identifiers).
        """
        import re

        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = name.replace("-", "_")
        if not re.match(r"^[a-z][a-z0-9_]{0,62}$", safe_name):
            raise ChatbotServiceError(
                code="INVALID_IDENTIFIER",
                message=f"Invalid identifier: {name}",
                suggestion="Use lowercase letters, numbers, hyphens, and underscores only",
            )

    def _database_exists(self, project: str) -> bool:
        """Check if chatbot database exists for a project."""
        db_name = self._chatbot_db_name(project)

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _create_chatbot_database(self, project: str) -> ChatbotDatabaseCredentials:
        """Create a PostgreSQL database for the chatbot service.

        Creates:
        - Database: {project}_chatbot_db
        - Role: {project}_chatbot_user with random password
        """
        db_name = self._chatbot_db_name(project)
        role_name = self._chatbot_role_name(project)

        # Validate identifiers
        self._validate_identifier(project)

        # Check if database already exists
        if self._database_exists(project):
            raise ChatbotServiceError(
                code="CHATBOT_DATABASE_EXISTS",
                message=f"Chatbot database '{db_name}' already exists",
                suggestion=(
                    "Disable chatbot first with "
                    "'hostkit chatbot disable' or "
                    "use a different project"
                ),
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

        return ChatbotDatabaseCredentials(
            database=db_name,
            username=role_name,
            password=password,
            host=self.config.postgres_host,
            port=self.config.postgres_port,
        )

    def _apply_schema(self, project: str, credentials: ChatbotDatabaseCredentials) -> None:
        """Apply the chatbot database schema to a project's chatbot database."""
        # Read schema from templates
        schema_path = Path("/var/lib/hostkit/templates/chatbot/schema.sql")
        if not schema_path.exists():
            # Try dev path
            schema_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "chatbot" / "schema.sql"
            )

        if not schema_path.exists():
            raise ChatbotServiceError(
                code="SCHEMA_NOT_FOUND",
                message="Chatbot schema.sql not found",
                suggestion="Ensure HostKit is properly installed",
            )

        schema_sql = schema_path.read_text()

        # Replace placeholder with actual project name
        schema_sql = schema_sql.replace("'_default'", f"'{project}'")

        try:
            # Connect to the chatbot database as the chatbot user
            conn = psycopg2.connect(
                host=credentials.host,
                port=credentials.port,
                user=credentials.username,
                password=credentials.password,
                database=credentials.database,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            try:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            finally:
                conn.close()

        except psycopg2.Error as e:
            raise ChatbotServiceError(
                code="SCHEMA_APPLY_FAILED",
                message=f"Failed to apply chatbot schema: {e}",
                suggestion="Check database connection and permissions",
            )

    def _deploy_chatbot_service(
        self, project: str, credentials: ChatbotDatabaseCredentials, api_key: str
    ) -> None:
        """Deploy the FastAPI chatbot service for a project.

        Steps:
        1. Copy chatbot service template files to /home/{project}/.chatbot/
        2. Create Python virtual environment
        3. Install dependencies
        4. Generate systemd service file
        5. Create log files
        """
        import shutil

        from jinja2 import Template

        chatbot_dir = self._chatbot_dir(project)
        templates_dir = Path("/var/lib/hostkit/templates/chatbot")
        if not templates_dir.exists():
            # Try dev path
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "chatbot"

        if not templates_dir.exists():
            raise ChatbotServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Chatbot service templates not found",
                suggestion="Ensure HostKit is properly installed",
            )

        # Step 1: Copy template files
        if chatbot_dir.exists():
            shutil.rmtree(chatbot_dir)

        chatbot_dir.mkdir(parents=True, exist_ok=True)

        # Get template context for rendering config.py
        chatbot_port = self._chatbot_port(project)
        primary_domain = f"{project}.hostkit.dev"
        base_url = f"https://{primary_domain}"

        template_context = {
            "project": project,
            "chatbot_port": chatbot_port,
            "domain": primary_domain,
            "base_url": base_url,
            "default_provider": self._llm_config.get("default_provider", "anthropic"),
            "default_model": self._llm_config.get("default_model", "claude-sonnet-4-20250514"),
        }

        # Copy all template files (render .j2 files as Jinja2 templates)
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file() and src_file.name != "schema.sql":
                rel_path = src_file.relative_to(templates_dir)
                dest_file = chatbot_dir / rel_path

                # Remove .j2 extension if present
                if dest_file.suffix == ".j2":
                    dest_file = dest_file.with_suffix("")

                dest_file.parent.mkdir(parents=True, exist_ok=True)

                if src_file.suffix == ".j2":
                    # Render as Jinja2 template
                    template = Template(src_file.read_text())
                    dest_file.write_text(template.render(**template_context))
                else:
                    shutil.copy2(src_file, dest_file)

        # Step 2: Create virtual environment
        venv_path = chatbot_dir / "venv"
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise ChatbotServiceError(
                code="VENV_CREATE_FAILED",
                message=(
                    "Failed to create virtual environment: "
                    f"{e.stderr.decode() if e.stderr else 'unknown error'}"
                ),
                suggestion="Ensure python3-venv is installed",
            )

        # Step 3: Install dependencies
        pip_path = venv_path / "bin" / "pip"
        requirements_path = chatbot_dir / "requirements.txt"
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_path)],
                check=True,
                capture_output=True,
                timeout=300,  # 5 minutes for pip install
            )
        except subprocess.CalledProcessError as e:
            raise ChatbotServiceError(
                code="PIP_INSTALL_FAILED",
                message=(
                    "Failed to install dependencies: "
                    f"{e.stderr.decode() if e.stderr else 'unknown error'}"
                ),
                suggestion="Check requirements.txt and network connectivity",
            )

        # Step 4: Generate systemd service file
        service_template_path = Path("/var/lib/hostkit/templates/chatbot.service.j2")
        if not service_template_path.exists():
            service_template_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "chatbot.service.j2"
            )

        if not service_template_path.exists():
            raise ChatbotServiceError(
                code="SERVICE_TEMPLATE_NOT_FOUND",
                message="Chatbot service systemd template not found",
                suggestion="Ensure HostKit is properly installed",
            )

        template = Template(service_template_path.read_text())
        service_content = template.render(
            project_name=project,
            chatbot_port=chatbot_port,
            chatbot_db_url=credentials.connection_url,
            anthropic_api_key=self._llm_config.get("anthropic_api_key", ""),
            openai_api_key=self._llm_config.get("openai_api_key", ""),
            api_key=api_key,
        )

        service_name = f"hostkit-{project}-chatbot"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_path.write_text(service_content)

        # Step 5: Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "chatbot.log").touch()
        (log_dir / "chatbot-error.log").touch()

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(chatbot_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "chatbot.log")],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "chatbot-error.log")],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def _configure_nginx_chatbot(self, project: str) -> None:
        """Configure Nginx to route /chatbot/* to the chatbot service.

        This adds a location block to the project's Nginx config.
        """
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.add_chatbot_location(project, self._chatbot_port(project))

    def _remove_nginx_chatbot(self, project: str) -> None:
        """Remove the /chatbot/* location block from Nginx config."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.remove_chatbot_location(project)

    def _start_chatbot_service(self, project: str) -> None:
        """Start and enable the chatbot service."""
        service_name = f"hostkit-{project}-chatbot"

        try:
            subprocess.run(
                ["systemctl", "enable", f"{service_name}.service"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "start", f"{service_name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise ChatbotServiceError(
                code="SERVICE_START_FAILED",
                message=(
                    "Failed to start chatbot service: "
                    f"{e.stderr.decode() if e.stderr else 'unknown error'}"
                ),
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _stop_chatbot_service(self, project: str) -> None:
        """Stop and disable the chatbot service."""
        service_name = f"hostkit-{project}-chatbot"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")

        if not service_path.exists():
            return  # Service doesn't exist

        try:
            subprocess.run(
                ["systemctl", "stop", f"{service_name}.service"],
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["systemctl", "disable", f"{service_name}.service"],
                capture_output=True,
            )
        except subprocess.SubprocessError:
            pass  # May fail if service is not running

    def _delete_chatbot_database(self, project: str) -> None:
        """Delete the chatbot database and role for a project."""
        db_name = self._chatbot_db_name(project)
        role_name = self._chatbot_role_name(project)

        # Validate identifiers
        self._validate_identifier(project)

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
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))

                # Drop role
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))

        finally:
            conn.close()

    def _update_project_env(self, project: str, api_key: str, chatbot_port: int) -> None:
        """Update the project's .env file with chatbot variables."""
        env_path = Path(f"/home/{project}/.env")

        chatbot_url = f"https://{project}.hostkit.dev/chatbot"

        # Read existing env
        env_content = ""
        if env_path.exists():
            env_content = env_path.read_text()

        # Remove any existing CHATBOT_ variables
        lines = [
            line
            for line in env_content.split("\n")
            if not line.startswith("CHATBOT_URL=") and not line.startswith("CHATBOT_API_KEY=")
        ]

        # Add new variables
        lines.append(f"CHATBOT_URL={chatbot_url}")
        lines.append(f"CHATBOT_API_KEY={api_key}")

        # Write back
        env_path.write_text("\n".join(lines))

        # Set ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

    def chatbot_is_enabled(self, project: str) -> bool:
        """Check if chatbot service is enabled for a project."""
        return self._database_exists(project)

    def enable_chatbot(self, project: str) -> dict[str, Any]:
        """Enable chatbot service for a project.

        Creates:
        - PostgreSQL database: {project}_chatbot_db
        - Database role: {project}_chatbot_user
        - Chatbot tables with schema
        - FastAPI chatbot service
        - Default configuration

        Returns:
            Dictionary with chatbot URL, port, and service details
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise ChatbotServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.chatbot_is_enabled(project):
            raise ChatbotServiceError(
                code="CHATBOT_ALREADY_ENABLED",
                message=f"Chatbot service is already enabled for '{project}'",
                suggestion="Use 'hostkit chatbot disable' first to reset configuration",
            )

        # Check LLM credentials
        if not self._llm_config.get("anthropic_api_key") and not self._llm_config.get(
            "openai_api_key"
        ):
            raise ChatbotServiceError(
                code="LLM_NOT_CONFIGURED",
                message="LLM credentials not configured",
                suggestion="Add LLM credentials to /etc/hostkit/llm.ini",
            )

        # Calculate chatbot port
        chatbot_port = self._chatbot_port(project)

        # Generate API key
        api_key = generate_api_key(project)

        try:
            # Step 1: Create chatbot database
            credentials = self._create_chatbot_database(project)

            # Step 2: Apply schema to chatbot database
            self._apply_schema(project, credentials)

            # Step 3: Deploy the FastAPI chatbot service
            self._deploy_chatbot_service(project, credentials, api_key)

            # Step 4: Configure Nginx to route /chatbot/* to chatbot service
            self._configure_nginx_chatbot(project)

            # Step 5: Start the chatbot service
            self._start_chatbot_service(project)

            # Step 6: Update project .env with chatbot variables
            self._update_project_env(project, api_key, chatbot_port)

            # Step 7: Regenerate nginx port mappings for wildcard routing
            from hostkit.services.project_service import ProjectService

            ProjectService()._regenerate_nginx_port_mappings()

            return {
                "chatbot_url": f"https://{project}.hostkit.dev/chatbot",
                "chatbot_port": chatbot_port,
                "chatbot_db": credentials.database,
                "chatbot_db_user": credentials.username,
                "api_key": api_key,
                "widget_script": f"https://{project}.hostkit.dev/chatbot/widget.js",
            }

        except Exception as e:
            # Rollback: clean up all created resources
            try:
                self._stop_chatbot_service(project)
            except Exception:
                pass
            try:
                self._remove_nginx_chatbot(project)
            except Exception:
                pass
            try:
                self._delete_chatbot_database(project)
            except Exception:
                pass
            try:
                import shutil

                chatbot_dir = self._chatbot_dir(project)
                if chatbot_dir.exists():
                    shutil.rmtree(chatbot_dir)
            except Exception:
                pass

            if isinstance(e, ChatbotServiceError):
                raise
            else:
                raise ChatbotServiceError(
                    code="CHATBOT_ENABLE_FAILED",
                    message=f"Failed to enable chatbot: {e}",
                    suggestion="Check PostgreSQL and LLM configuration",
                )

    def disable_chatbot(self, project: str, force: bool = False) -> None:
        """Disable chatbot service for a project.

        Removes:
        - PostgreSQL database and role
        - Chatbot service files
        - Systemd service

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise ChatbotServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if chatbot is enabled
        if not self.chatbot_is_enabled(project):
            raise ChatbotServiceError(
                code="CHATBOT_NOT_ENABLED",
                message=f"Chatbot service is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise ChatbotServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable chatbot service",
                suggestion=f"Add --force to confirm: 'hostkit chatbot disable {project} --force'",
            )

        # Step 1: Stop and remove chatbot service
        self._stop_chatbot_service(project)

        # Step 2: Remove Nginx chatbot configuration
        self._remove_nginx_chatbot(project)

        # Step 3: Remove systemd service file
        service_name = f"hostkit-{project}-chatbot"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # Step 4: Delete chatbot database and role
        self._delete_chatbot_database(project)

        # Step 5: Remove chatbot directory
        import shutil

        chatbot_dir = self._chatbot_dir(project)
        if chatbot_dir.exists():
            shutil.rmtree(chatbot_dir)

    def get_api_key(self, project: str) -> str | None:
        """Retrieve the chatbot API key from the project's .env file.

        Returns:
            The API key if found, None otherwise
        """
        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            return None

        try:
            content = env_path.read_text()
            for line in content.split("\n"):
                if line.startswith("CHATBOT_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except PermissionError:
            return None

        return None

    def get_chatbot_status(self, project: str, show_key: bool = False) -> dict[str, Any]:
        """Get chatbot service status for a project.

        Args:
            project: Project name
            show_key: If True, include the API key in the response

        Returns:
            Dictionary with chatbot configuration and statistics
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise ChatbotServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        if not self.chatbot_is_enabled(project):
            result = {
                "enabled": False,
                "project": project,
                "chatbot_url": None,
                "chatbot_port": None,
                "widget_script": None,
                "conversations_total": 0,
                "messages_total": 0,
            }
            if show_key:
                result["api_key"] = None
            return result

        # Get config from database
        config = self._get_chatbot_config(project)

        result = {
            "enabled": True,
            "project": project,
            "chatbot_url": f"https://{project}.hostkit.dev/chatbot",
            "chatbot_port": self._chatbot_port(project),
            "widget_script": f"https://{project}.hostkit.dev/chatbot/widget.js",
            "name": config.get("name", "Assistant"),
            "greeting": config.get("greeting", "Hi! How can I help you today?"),
            "position": config.get("position", "bottom-right"),
            "theme": config.get("theme", "light"),
            "primary_color": config.get("primary_color", "#6366f1"),
            "system_prompt": config.get("system_prompt"),
            "suggested_questions": config.get("suggested_questions", []),
            "model": config.get("model", "claude-sonnet-4-20250514"),
            "cta_enabled": config.get("cta_enabled", False),
            "cta_text": config.get("cta_text"),
            "cta_url": config.get("cta_url"),
            "cta_after_messages": config.get("cta_after_messages", 3),
            "conversations_total": config.get("conversations_total", 0),
            "messages_total": config.get("messages_total", 0),
        }

        if show_key:
            result["api_key"] = self.get_api_key(project)

        return result

    def _get_chatbot_db_connection(self, project: str) -> psycopg2.extensions.connection:
        """Get a connection to the project's chatbot database as admin."""
        db_name = self._chatbot_db_name(project)

        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database=db_name,
            )
            return conn
        except psycopg2.OperationalError as e:
            raise ChatbotServiceError(
                code="CHATBOT_DB_CONNECTION_FAILED",
                message=f"Failed to connect to chatbot database: {e}",
                suggestion="Check if chatbot is enabled for this project",
            )

    def _get_chatbot_config(self, project: str) -> dict[str, Any]:
        """Get chatbot configuration from database."""
        conn = self._get_chatbot_db_connection(project)
        try:
            with conn.cursor() as cur:
                # Get config from chatbot_configs table
                cur.execute(
                    """
                    SELECT name, greeting, placeholder, position, theme, primary_color,
                           system_prompt, suggested_questions, cta_enabled, cta_text,
                           cta_url, cta_after_messages, llm_provider, llm_model,
                           max_tokens, temperature
                    FROM chatbot_configs
                    WHERE project = %s OR project = '_default'
                    ORDER BY CASE WHEN project = %s THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    [project, project],
                )
                row = cur.fetchone()

                if row:
                    suggested_questions = row[7] or []
                    if isinstance(suggested_questions, str):
                        import json

                        try:
                            suggested_questions = json.loads(suggested_questions)
                        except Exception:
                            suggested_questions = []

                    config = {
                        "name": row[0],
                        "greeting": row[1],
                        "placeholder": row[2],
                        "position": row[3],
                        "theme": row[4],
                        "primary_color": row[5],
                        "system_prompt": row[6],
                        "suggested_questions": suggested_questions,
                        "cta_enabled": row[8],
                        "cta_text": row[9],
                        "cta_url": row[10],
                        "cta_after_messages": row[11],
                        "llm_provider": row[12],
                        "model": row[13],
                        "max_tokens": row[14],
                        "temperature": float(row[15]) if row[15] else 0.7,
                    }
                else:
                    config = {
                        "name": "Assistant",
                        "greeting": "Hi! How can I help you today?",
                        "placeholder": "Type your message...",
                        "position": "bottom-right",
                        "theme": "light",
                        "primary_color": "#6366f1",
                        "system_prompt": None,
                        "suggested_questions": [],
                        "cta_enabled": False,
                        "cta_text": None,
                        "cta_url": None,
                        "cta_after_messages": 3,
                        "llm_provider": "anthropic",
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 1024,
                        "temperature": 0.7,
                    }

                # Get conversation and message counts
                cur.execute(
                    "SELECT COUNT(*) FROM chatbot_conversations WHERE project = %s",
                    [project],
                )
                config["conversations_total"] = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM chatbot_messages WHERE project = %s",
                    [project],
                )
                config["messages_total"] = cur.fetchone()[0]

                return config
        finally:
            conn.close()

    def update_config(
        self,
        project: str,
        name: str | None = None,
        system_prompt: str | None = None,
        suggested_questions: list[str] | None = None,
        position: str | None = None,
        primary_color: str | None = None,
        theme: str | None = None,
        cta_text: str | None = None,
        cta_url: str | None = None,
        cta_after: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Update chatbot configuration for a project.

        Returns the updated configuration.
        """
        if not self.chatbot_is_enabled(project):
            raise ChatbotServiceError(
                code="CHATBOT_NOT_ENABLED",
                message=f"Chatbot service is not enabled for '{project}'",
                suggestion=f"Enable chatbot first with 'hostkit chatbot enable {project}'",
            )

        conn = self._get_chatbot_db_connection(project)
        try:
            with conn.cursor() as cur:
                # Check if project-specific config exists
                cur.execute(
                    "SELECT id FROM chatbot_configs WHERE project = %s",
                    [project],
                )
                existing = cur.fetchone()

                if not existing:
                    # Create project-specific config by copying from default
                    cur.execute(
                        """
                        INSERT INTO chatbot_configs (project, name, greeting, placeholder, position,
                            theme, primary_color, system_prompt, suggested_questions, cta_enabled,
                            cta_text, cta_url, cta_after_messages, llm_provider, llm_model)
                        SELECT %s, name, greeting, placeholder, position, theme, primary_color,
                               system_prompt, suggested_questions, cta_enabled, cta_text, cta_url,
                               cta_after_messages, llm_provider, llm_model
                        FROM chatbot_configs WHERE project = '_default'
                        RETURNING id
                        """,
                        [project],
                    )
                    conn.commit()

                # Build dynamic UPDATE query with only provided fields
                updates = []
                params = []

                if name is not None:
                    updates.append("name = %s")
                    params.append(name)
                if system_prompt is not None:
                    updates.append("system_prompt = %s")
                    params.append(system_prompt)
                if suggested_questions is not None:
                    import json

                    updates.append("suggested_questions = %s::jsonb")
                    params.append(json.dumps(suggested_questions))
                if position is not None:
                    updates.append("position = %s")
                    params.append(position)
                if primary_color is not None:
                    updates.append("primary_color = %s")
                    params.append(primary_color)
                if theme is not None:
                    updates.append("theme = %s")
                    params.append(theme)
                if cta_text is not None:
                    updates.append("cta_text = %s")
                    params.append(cta_text)
                    updates.append("cta_enabled = true")
                if cta_url is not None:
                    updates.append("cta_url = %s")
                    params.append(cta_url)
                if cta_after is not None:
                    updates.append("cta_after_messages = %s")
                    params.append(cta_after)
                if model is not None:
                    updates.append("llm_model = %s")
                    params.append(model)

                if updates:
                    updates.append("updated_at = NOW()")
                    query = f"UPDATE chatbot_configs SET {', '.join(updates)} WHERE project = %s"
                    params.append(project)
                    cur.execute(query, params)
                    conn.commit()

            # Return the updated config
            return self._get_chatbot_config(project)
        finally:
            conn.close()

    def get_stats(self, project: str) -> dict[str, Any]:
        """Get chatbot statistics for a project.

        Returns conversation and message counts.
        """
        if not self.chatbot_is_enabled(project):
            raise ChatbotServiceError(
                code="CHATBOT_NOT_ENABLED",
                message=f"Chatbot service is not enabled for '{project}'",
                suggestion=f"Enable chatbot first with 'hostkit chatbot enable {project}'",
            )

        conn = self._get_chatbot_db_connection(project)
        try:
            with conn.cursor() as cur:
                # Total conversations
                cur.execute(
                    "SELECT COUNT(*) FROM chatbot_conversations WHERE project = %s",
                    [project],
                )
                conversations_total = cur.fetchone()[0]

                # Conversations today
                cur.execute(
                    """
                    SELECT COUNT(*) FROM chatbot_conversations
                    WHERE project = %s AND started_at >= CURRENT_DATE
                    """,
                    [project],
                )
                conversations_today = cur.fetchone()[0]

                # Total messages
                cur.execute(
                    "SELECT COUNT(*) FROM chatbot_messages WHERE project = %s",
                    [project],
                )
                messages_total = cur.fetchone()[0]

                # Messages today
                cur.execute(
                    """
                    SELECT COUNT(*) FROM chatbot_messages
                    WHERE project = %s AND created_at >= CURRENT_DATE
                    """,
                    [project],
                )
                messages_today = cur.fetchone()[0]

                # Average messages per conversation
                avg_messages = 0.0
                if conversations_total > 0:
                    avg_messages = messages_total / conversations_total

                # CTA stats
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE cta_shown = true) as cta_shown,
                        COUNT(*) FILTER (WHERE cta_clicked = true) as cta_clicked
                    FROM chatbot_conversations
                    WHERE project = %s
                    """,
                    [project],
                )
                cta_row = cur.fetchone()
                cta_shown = cta_row[0] if cta_row else 0
                cta_clicked = cta_row[1] if cta_row else 0

                return {
                    "project": project,
                    "conversations_total": conversations_total,
                    "conversations_today": conversations_today,
                    "messages_total": messages_total,
                    "messages_today": messages_today,
                    "avg_messages_per_conversation": round(avg_messages, 1),
                    "cta_shown": cta_shown,
                    "cta_clicked": cta_clicked,
                    "cta_click_rate": round(cta_clicked / cta_shown * 100, 1)
                    if cta_shown > 0
                    else 0.0,
                }
        finally:
            conn.close()

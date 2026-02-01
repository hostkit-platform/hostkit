"""SMS service management for HostKit.

Provides per-project SMS messaging via Twilio with consent tracking,
templates, and conversational AI integration.
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

# Register SMS service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="sms",
        description=(
            "Transactional and conversational SMS via Twilio (templates, consent tracking, AI)"
        ),
        provision_flag="--with-sms",
        enable_command="hostkit sms enable {project}",
        env_vars_provided=["SMS_URL", "SMS_WEBHOOK_SECRET", "TWILIO_PHONE_NUMBER"],
        related_commands=[
            "sms enable",
            "sms disable",
            "sms status",
            "sms send",
            "sms template",
            "sms logs",
        ],
    )
)


@dataclass
class SMSDatabaseCredentials:
    """SMS database connection credentials."""

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


class SMSServiceError(Exception):
    """Base exception for SMS service errors."""

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


def generate_webhook_secret() -> str:
    """Generate a webhook secret in the format whsec_xxxx."""
    random_part = secrets.token_urlsafe(32)
    return f"whsec_{random_part}"


class SMSService:
    """Service for managing per-project SMS services."""

    def __init__(self) -> None:
        """Initialize the SMS service."""
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        # Load Twilio credentials from /etc/hostkit/twilio.ini
        self._twilio_account_sid, self._twilio_auth_token = self._load_twilio_credentials()

    def _load_twilio_credentials(self) -> tuple[str, str]:
        """Load Twilio credentials from /etc/hostkit/twilio.ini."""
        twilio_ini_path = Path("/etc/hostkit/twilio.ini")
        if not twilio_ini_path.exists():
            return "", ""  # Return empty, will error on enable if not configured

        try:
            import configparser

            config = configparser.ConfigParser()
            config.read(twilio_ini_path)
            account_sid = config.get("twilio", "account_sid", fallback="")
            auth_token = config.get("twilio", "auth_token", fallback="")
            return account_sid, auth_token
        except Exception:
            return "", ""

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
            raise SMSServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL: {e}",
                suggestion="Check PostgreSQL is running and credentials are correct",
            )

    def _sms_db_name(self, project: str) -> str:
        """Generate SMS database name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_sms_db"

    def _sms_role_name(self, project: str) -> str:
        """Generate SMS database role name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_sms_user"

    def _sms_port(self, project: str) -> int:
        """Calculate SMS service port from project port.

        SMS service runs on project_port + 3000.
        (Auth uses +1000, payment uses +2000, SMS uses +3000)
        """
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise SMSServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return project_data["port"] + 3000

    def _sms_dir(self, project: str) -> Path:
        """Get the SMS service directory for a project."""
        return Path(f"/home/{project}/.sms")

    def _validate_identifier(self, name: str) -> None:
        """Validate a PostgreSQL identifier to prevent SQL injection.

        Accepts project names with hyphens (they get converted to underscores
        for PostgreSQL identifiers).
        """
        import re

        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = name.replace("-", "_")
        if not re.match(r"^[a-z][a-z0-9_]{0,62}$", safe_name):
            raise SMSServiceError(
                code="INVALID_IDENTIFIER",
                message=f"Invalid identifier: {name}",
                suggestion="Use lowercase letters, numbers, hyphens, and underscores only",
            )

    def _database_exists(self, project: str) -> bool:
        """Check if SMS database exists for a project."""
        db_name = self._sms_db_name(project)

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _create_sms_database(self, project: str) -> SMSDatabaseCredentials:
        """Create a PostgreSQL database for the SMS service.

        Creates:
        - Database: {project}_sms_db
        - Role: {project}_sms_user with random password
        """
        db_name = self._sms_db_name(project)
        role_name = self._sms_role_name(project)

        # Validate identifiers
        self._validate_identifier(project)

        # Check if database already exists
        if self._database_exists(project):
            raise SMSServiceError(
                code="SMS_DATABASE_EXISTS",
                message=f"SMS database '{db_name}' already exists",
                suggestion=(
                    "Disable SMS first with 'hostkit sms disable' or use a different project"
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

        return SMSDatabaseCredentials(
            database=db_name,
            username=role_name,
            password=password,
            host=self.config.postgres_host,
            port=self.config.postgres_port,
        )

    def _apply_schema(self, project: str, credentials: SMSDatabaseCredentials) -> None:
        """Apply the SMS database schema to a project's SMS database."""
        # Read schema from templates
        schema_path = Path("/var/lib/hostkit/templates/sms/schema.sql")
        if not schema_path.exists():
            # Try dev path
            schema_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "sms" / "schema.sql"
            )

        if not schema_path.exists():
            raise SMSServiceError(
                code="SCHEMA_NOT_FOUND",
                message="SMS schema.sql not found",
                suggestion="Ensure HostKit is properly installed",
            )

        schema_sql = schema_path.read_text()

        # Replace _default with actual project name for templates
        schema_sql = schema_sql.replace("'_default'", f"'{project}'")

        try:
            # Connect to the SMS database as the SMS user
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
            raise SMSServiceError(
                code="SCHEMA_APPLY_FAILED",
                message=f"Failed to apply SMS schema: {e}",
                suggestion="Check database connection and permissions",
            )

    def _get_phone_number(self, project: str, phone_number: str | None) -> str:
        """Get phone number for SMS service (shared with voice or specified)."""
        if phone_number:
            return phone_number

        # Try to get from voice service
        # Query project's voice config if exists
        # For now, return placeholder - will integrate with voice service
        return "+15551234567"  # TODO: Get from voice service or require --phone-number

    def _deploy_sms_service(
        self,
        project: str,
        credentials: SMSDatabaseCredentials,
        phone_number: str,
        webhook_secret: str,
    ) -> None:
        """Deploy the FastAPI SMS service for a project.

        Steps:
        1. Copy SMS service template files to /home/{project}/.sms/
        2. Create Python virtual environment
        3. Install dependencies
        4. Generate systemd service file
        5. Create log files
        """
        import shutil

        from jinja2 import Template

        sms_dir = self._sms_dir(project)
        templates_dir = Path("/var/lib/hostkit/templates/sms")
        if not templates_dir.exists():
            # Try dev path
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "sms"

        if not templates_dir.exists():
            raise SMSServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="SMS service templates not found",
                suggestion="Ensure HostKit is properly installed",
            )

        # Step 1: Copy template files
        if sms_dir.exists():
            shutil.rmtree(sms_dir)

        sms_dir.mkdir(parents=True, exist_ok=True)

        # Get template context for rendering config.py
        sms_port = self._sms_port(project)
        primary_domain = f"{project}.hostkit.dev"
        base_url = f"https://{primary_domain}"

        template_context = {
            "project": project,
            "sms_port": sms_port,
            "domain": primary_domain,
            "base_url": base_url,
            "phone_number": phone_number,
        }

        # Copy all template files (render config.py.j2 as Jinja2 template)
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file() and src_file.name != "schema.sql":
                rel_path = src_file.relative_to(templates_dir)
                dest_file = sms_dir / rel_path

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
        venv_path = sms_dir / "venv"
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise SMSServiceError(
                code="VENV_CREATE_FAILED",
                message=f"Failed to create virtual environment: {stderr}",
                suggestion="Ensure python3-venv is installed",
            )

        # Step 3: Install dependencies
        pip_path = venv_path / "bin" / "pip"
        requirements_path = sms_dir / "requirements.txt"
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_path)],
                check=True,
                capture_output=True,
                timeout=300,  # 5 minutes for pip install
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise SMSServiceError(
                code="PIP_INSTALL_FAILED",
                message=f"Failed to install dependencies: {stderr}",
                suggestion="Check requirements.txt and network connectivity",
            )

        # Step 4: Generate systemd service file
        service_template_path = Path("/var/lib/hostkit/templates/sms.service.j2")
        if not service_template_path.exists():
            service_template_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "sms.service.j2"
            )

        if not service_template_path.exists():
            raise SMSServiceError(
                code="SERVICE_TEMPLATE_NOT_FOUND",
                message="SMS service systemd template not found",
                suggestion="Ensure HostKit is properly installed",
            )

        template = Template(service_template_path.read_text())
        service_content = template.render(
            project_name=project,
            sms_port=sms_port,
            sms_db_url=credentials.connection_url,
            twilio_account_sid=self._twilio_account_sid,
            twilio_auth_token=self._twilio_auth_token,
            phone_number=phone_number,
            webhook_secret=webhook_secret,
        )

        service_name = f"hostkit-{project}-sms"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_path.write_text(service_content)

        # Step 5: Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "sms.log").touch()
        (log_dir / "sms-error.log").touch()

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(sms_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "sms.log")],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "sms-error.log")],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def _configure_nginx_sms(self, project: str) -> None:
        """Configure Nginx to route /api/sms/* to the SMS service.

        This adds a location block to the project's Nginx config.
        """
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.add_sms_location(project, self._sms_port(project))

    def _remove_nginx_sms(self, project: str) -> None:
        """Remove the /api/sms/* location block from Nginx config."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.remove_sms_location(project)

    def _start_sms_service(self, project: str) -> None:
        """Start and enable the SMS service."""
        service_name = f"hostkit-{project}-sms"

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
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise SMSServiceError(
                code="SERVICE_START_FAILED",
                message=f"Failed to start SMS service: {stderr}",
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _stop_sms_service(self, project: str) -> None:
        """Stop and disable the SMS service."""
        service_name = f"hostkit-{project}-sms"
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

    def _delete_sms_database(self, project: str) -> None:
        """Delete the SMS database and role for a project."""
        db_name = self._sms_db_name(project)
        role_name = self._sms_role_name(project)

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

    def sms_is_enabled(self, project: str) -> bool:
        """Check if SMS service is enabled for a project."""
        return self._database_exists(project)

    def enable_sms(
        self,
        project: str,
        phone_number: str | None = None,
        ai_enabled: bool = False,
        default_agent: str | None = None,
    ) -> dict[str, Any]:
        """Enable SMS service for a project.

        Creates:
        - PostgreSQL database: {project}_sms_db
        - Database role: {project}_sms_user
        - SMS tables with schema
        - FastAPI SMS service
        - Default message templates

        Returns:
            Dictionary with phone number, SMS port, and service details
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise SMSServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.sms_is_enabled(project):
            raise SMSServiceError(
                code="SMS_ALREADY_ENABLED",
                message=f"SMS service is already enabled for '{project}'",
                suggestion="Use 'hostkit sms disable' first to reset configuration",
            )

        # Check Twilio credentials
        if not self._twilio_account_sid or not self._twilio_auth_token:
            raise SMSServiceError(
                code="TWILIO_NOT_CONFIGURED",
                message="Twilio credentials not configured",
                suggestion="Add Twilio credentials to /etc/hostkit/twilio.ini",
            )

        # Calculate SMS port
        sms_port = self._sms_port(project)

        # Get phone number
        resolved_phone_number = self._get_phone_number(project, phone_number)

        # Generate webhook secret
        webhook_secret = generate_webhook_secret()

        try:
            # Step 1: Create SMS database
            credentials = self._create_sms_database(project)

            # Step 2: Apply schema to SMS database
            self._apply_schema(project, credentials)

            # Step 3: Deploy the FastAPI SMS service
            self._deploy_sms_service(project, credentials, resolved_phone_number, webhook_secret)

            # Step 4: Configure Nginx to route /api/sms/* to SMS service
            self._configure_nginx_sms(project)

            # Step 5: Start the SMS service
            self._start_sms_service(project)

            # Step 6: Regenerate nginx port mappings for wildcard routing
            from hostkit.services.project_service import ProjectService

            ProjectService()._regenerate_nginx_port_mappings()

            return {
                "phone_number": resolved_phone_number,
                "sms_port": sms_port,
                "sms_db": credentials.database,
                "sms_db_user": credentials.username,
                "webhook_url": f"https://{project}.hostkit.dev/api/sms/webhook/inbound",
                "templates_created": 11,  # Default templates from schema
            }

        except Exception as e:
            # Rollback: clean up all created resources
            try:
                self._stop_sms_service(project)
            except Exception:
                pass
            try:
                self._remove_nginx_sms(project)
            except Exception:
                pass
            try:
                self._delete_sms_database(project)
            except Exception:
                pass
            try:
                import shutil

                sms_dir = self._sms_dir(project)
                if sms_dir.exists():
                    shutil.rmtree(sms_dir)
            except Exception:
                pass

            if isinstance(e, SMSServiceError):
                raise
            else:
                raise SMSServiceError(
                    code="SMS_ENABLE_FAILED",
                    message=f"Failed to enable SMS: {e}",
                    suggestion="Check PostgreSQL and Twilio configuration",
                )

    def disable_sms(self, project: str, force: bool = False) -> None:
        """Disable SMS service for a project.

        Removes:
        - PostgreSQL database and role
        - SMS service files
        - Systemd service

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise SMSServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if SMS is enabled
        if not self.sms_is_enabled(project):
            raise SMSServiceError(
                code="SMS_NOT_ENABLED",
                message=f"SMS service is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise SMSServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable SMS service",
                suggestion=f"Add --force to confirm: 'hostkit sms disable {project} --force'",
            )

        # Step 1: Stop and remove SMS service
        self._stop_sms_service(project)

        # Step 2: Remove Nginx SMS configuration
        self._remove_nginx_sms(project)

        # Step 3: Remove systemd service file
        service_name = f"hostkit-{project}-sms"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # Step 4: Delete SMS database and role
        self._delete_sms_database(project)

        # Step 5: Remove SMS directory
        import shutil

        sms_dir = self._sms_dir(project)
        if sms_dir.exists():
            shutil.rmtree(sms_dir)

    def get_sms_status(self, project: str) -> dict[str, Any]:
        """Get SMS service status for a project.

        Returns:
            Dictionary with SMS configuration and statistics
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise SMSServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        if not self.sms_is_enabled(project):
            return {
                "enabled": False,
                "project": project,
                "phone_number": None,
                "sms_port": None,
                "ai_enabled": False,
                "default_agent": None,
                "messages_today": 0,
                "active_conversations": 0,
                "webhook_url": None,
            }

        # TODO: Query SMS database for actual stats
        # For now, return placeholder data
        return {
            "enabled": True,
            "project": project,
            "phone_number": "+15551234567",
            "sms_port": self._sms_port(project),
            "ai_enabled": False,
            "default_agent": None,
            "messages_today": 0,
            "active_conversations": 0,
            "webhook_url": f"https://{project}.hostkit.dev/api/sms/webhook/inbound",
        }

    def send_sms(
        self,
        project: str,
        to: str,
        template: str | None = None,
        body: str | None = None,
        variables: dict[str, Any] | None = None,
        skip_consent_check: bool = False,
    ) -> dict[str, Any]:
        """Send an SMS message.

        Args:
            project: Project name
            to: Recipient phone number (E.164 format)
            template: Template name (optional)
            body: Raw message body (optional)
            variables: Template variables (optional)
            skip_consent_check: Skip consent check (OTP only)

        Returns:
            Dictionary with message_id, status, and segments
        """
        # TODO: Implement actual SMS sending via Twilio
        # For now, return placeholder
        import uuid

        return {
            "message_id": str(uuid.uuid4()),
            "status": "queued",
            "segments": 1,
        }

    def list_templates(self, project: str) -> list[dict[str, Any]]:
        """List all templates for a project."""
        # TODO: Query SMS database
        return []

    def create_template(
        self, project: str, name: str, body: str, category: str, include_opt_out: bool
    ) -> dict[str, Any]:
        """Create a new SMS template."""
        # TODO: Insert into SMS database
        return {"name": name, "category": category, "created": True}

    def get_template(self, project: str, name: str) -> dict[str, Any]:
        """Get a specific template."""
        # TODO: Query SMS database
        return {
            "name": name,
            "body": "Template body",
            "category": "transactional",
            "include_opt_out": True,
            "times_sent": 0,
        }

    def update_template(
        self, project: str, name: str, body: str | None = None, category: str | None = None
    ) -> dict[str, Any]:
        """Update an existing template."""
        # TODO: Update in SMS database
        return {"name": name, "updated": True}

    def delete_template(self, project: str, name: str) -> None:
        """Delete a template."""
        # TODO: Delete from SMS database
        pass

"""Booking service management for HostKit.

Provides per-project appointment scheduling with provider pooling and room management.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register booking service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="booking",
        description="Time-first appointment scheduling (providers, rooms, intake forms, reminders)",
        provision_flag="--with-booking",
        enable_command="hostkit booking enable {project}",
        env_vars_provided=["BOOKING_API_URL", "BOOKING_ADMIN_URL", "BOOKING_DOCS_URL"],
        related_commands=[
            "booking enable",
            "booking disable",
            "booking status",
            "booking seed",
            "booking logs",
        ],
    )
)


class BookingServiceError(Exception):
    """Base exception for booking service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class BookingService:
    """Service for managing per-project booking services."""

    def __init__(self) -> None:
        """Initialize the booking service."""
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

    def _get_project_db_connection(self, project: str) -> psycopg2.extensions.connection:
        """Get a connection to the project's PostgreSQL database."""
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise BookingServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Get project database name
        db_name = f"{project}_db"

        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database=db_name,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            return conn
        except psycopg2.OperationalError as e:
            raise BookingServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to project database: {e}",
                suggestion=f"Ensure project '{project}' has a database created with --with-db",
            )

    def _booking_port(self, project: str) -> int:
        """Calculate booking service port from project port.

        Booking service runs on project_port + 4000.
        (Auth uses +1000, Payment +2000, SMS +3000, Booking +4000)
        """
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise BookingServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return project_data["port"] + 4000

    def _booking_dir(self, project: str) -> Path:
        """Get the booking service directory for a project."""
        return Path(f"/home/{project}/.booking")

    def _table_exists(self, project: str, table_name: str) -> bool:
        """Check if a table exists in the project's database."""
        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = %s", [table_name]
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _apply_schema(self, project: str) -> None:
        """Apply the booking database schema to a project's database."""
        # Read schema from templates
        schema_path = Path("/var/lib/hostkit/templates/booking/schema.sql")
        if not schema_path.exists():
            # Try dev path
            schema_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "booking" / "schema.sql"
            )

        if not schema_path.exists():
            raise BookingServiceError(
                code="SCHEMA_NOT_FOUND",
                message="Booking schema.sql not found",
                suggestion="Ensure HostKit is properly installed",
            )

        schema_sql = schema_path.read_text()

        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                cur.execute(schema_sql)

                # Grant permissions to project database user on
                # all booking tables. Tables are created by hostkit
                # admin, but the FastAPI service runs as {project}_user
                project_db_user = f"{project}_user"
                booking_tables = [
                    "booking_configs",
                    "providers",
                    "rooms",
                    "services",
                    "provider_services",
                    "room_services",
                    "provider_schedules",
                    "schedule_overrides",
                    "customers",
                    "appointments",
                    "intake_templates",
                    "intake_forms",
                    "service_intake_forms",
                    "notifications",
                ]
                for table in booking_tables:
                    cur.execute(
                        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                            sql.Identifier(table), sql.Identifier(project_db_user)
                        )
                    )

                # Grant usage on all sequences (for auto-generated UUIDs)
                cur.execute(
                    sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                        sql.Identifier(project_db_user)
                    )
                )
        except psycopg2.Error as e:
            raise BookingServiceError(
                code="SCHEMA_APPLY_FAILED",
                message=f"Failed to apply booking schema: {e}",
                suggestion="Check database connection and permissions",
            )
        finally:
            conn.close()

        # Apply any migrations after the base schema
        self._apply_migrations(project)

    def _apply_migrations(self, project: str) -> None:
        """Apply booking database migrations to handle schema updates.

        Migrations are SQL files in templates/booking/migrations/ directory,
        sorted alphabetically and executed in order. Each migration should be
        idempotent (safe to run multiple times).
        """
        # Find migrations directory
        migrations_path = Path("/var/lib/hostkit/templates/booking/migrations")
        if not migrations_path.exists():
            # Try dev path
            migrations_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "booking" / "migrations"
            )

        if not migrations_path.exists():
            return  # No migrations directory is fine

        # Get migration files sorted by name
        migration_files = sorted(migrations_path.glob("*.sql"))
        if not migration_files:
            return

        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                for migration_file in migration_files:
                    migration_sql = migration_file.read_text()
                    cur.execute(migration_sql)
        except psycopg2.Error as e:
            raise BookingServiceError(
                code="MIGRATION_FAILED",
                message=f"Failed to apply migration {migration_file.name}: {e}",
                suggestion="Check migration SQL syntax",
            )
        finally:
            conn.close()

    def _verify_permissions(self, project: str) -> None:
        """Verify that the project database user has proper permissions on booking tables.

        This prevents the race condition where the service starts before grants are committed.
        """
        project_db_user = f"{project}_user"
        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                # Check that project_user has SELECT privilege on booking_configs
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.table_privileges
                    WHERE grantee = %s
                      AND table_name = 'booking_configs'
                      AND privilege_type = 'SELECT'
                    """,
                    [project_db_user],
                )
                if not cur.fetchone():
                    raise BookingServiceError(
                        code="PERMISSION_VERIFICATION_FAILED",
                        message=(
                            f"Database user '{project_db_user}'"
                            " does not have SELECT permission"
                            " on booking_configs"
                        ),
                        suggestion=(
                            "This may be a race condition."
                            " Try running 'hostkit booking"
                            " disable --force' and then"
                            " 'hostkit booking enable' again"
                        ),
                    )

                # Verify by actually testing a query as the project user
                # Use SET ROLE to temporarily switch to the project user
                cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(project_db_user)))
                try:
                    cur.execute("SELECT 1 FROM booking_configs LIMIT 0")
                finally:
                    cur.execute("RESET ROLE")

        except psycopg2.errors.InsufficientPrivilege as e:
            raise BookingServiceError(
                code="PERMISSION_VERIFICATION_FAILED",
                message=f"Permission verification failed: {e}",
                suggestion=(
                    "This may be a race condition."
                    " Try running 'hostkit booking"
                    " disable --force' and then"
                    " 'hostkit booking enable' again"
                ),
            )
        except psycopg2.Error:
            # Don't fail on other errors - just log and continue
            pass
        finally:
            conn.close()

    def _drop_schema(self, project: str) -> None:
        """Drop all booking tables from the project's database."""
        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                # Drop tables in reverse dependency order
                cur.execute("""
                    DROP TABLE IF EXISTS notifications CASCADE;
                    DROP TABLE IF EXISTS service_intake_forms CASCADE;
                    DROP TABLE IF EXISTS intake_forms CASCADE;
                    DROP TABLE IF EXISTS intake_templates CASCADE;
                    DROP TABLE IF EXISTS appointments CASCADE;
                    DROP TABLE IF EXISTS schedule_overrides CASCADE;
                    DROP TABLE IF EXISTS provider_schedules CASCADE;
                    DROP TABLE IF EXISTS room_services CASCADE;
                    DROP TABLE IF EXISTS provider_services CASCADE;
                    DROP TABLE IF EXISTS services CASCADE;
                    DROP TABLE IF EXISTS rooms CASCADE;
                    DROP TABLE IF EXISTS customers CASCADE;
                    DROP TABLE IF EXISTS providers CASCADE;
                    DROP TABLE IF EXISTS booking_configs CASCADE;
                """)
        except psycopg2.Error as e:
            raise BookingServiceError(
                code="SCHEMA_DROP_FAILED",
                message=f"Failed to drop booking schema: {e}",
                suggestion="Check database connection and permissions",
            )
        finally:
            conn.close()

    def _deploy_booking_service(self, project: str) -> None:
        """Deploy the FastAPI booking service for a project.

        Steps:
        1. Copy booking service template files to /home/{project}/.booking/
        2. Create Python virtual environment
        3. Install dependencies
        4. Generate systemd service file
        5. Create log files
        """
        import shutil

        from jinja2 import Template

        booking_dir = self._booking_dir(project)
        templates_dir = Path("/var/lib/hostkit/templates/booking")
        if not templates_dir.exists():
            # Try dev path
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "booking"

        if not templates_dir.exists():
            raise BookingServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Booking service templates not found",
                suggestion="Ensure HostKit is properly installed",
            )

        # Step 1: Copy template files
        if booking_dir.exists():
            shutil.rmtree(booking_dir)

        booking_dir.mkdir(parents=True, exist_ok=True)

        # Get template context for rendering config.py
        booking_port = self._booking_port(project)
        primary_domain = f"{project}.hostkit.dev"
        api_url = f"https://{primary_domain}/api/booking"
        admin_url = f"https://{primary_domain}/api/admin"
        docs_url = f"https://{primary_domain}/docs"

        # Get project database URL from .env
        env_path = Path(f"/home/{project}/.env")
        database_url = ""
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    database_url = line.split("=", 1)[1].strip()
                    break

        template_context = {
            "project": project,
            "booking_port": booking_port,
            "domain": primary_domain,
            "api_url": api_url,
            "admin_url": admin_url,
            "docs_url": docs_url,
            "database_url": database_url,
        }

        # Copy all template files (render .j2 files as Jinja2 templates)
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file() and src_file.name != "schema.sql":
                rel_path = src_file.relative_to(templates_dir)
                dest_file = booking_dir / rel_path

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
        venv_path = booking_dir / "venv"
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise BookingServiceError(
                code="VENV_CREATE_FAILED",
                message=(
                    "Failed to create virtual environment: "
                    + (e.stderr.decode() if e.stderr else "unknown error")
                ),
                suggestion="Ensure python3-venv is installed",
            )

        # Step 3: Install dependencies
        pip_path = venv_path / "bin" / "pip"
        requirements_path = booking_dir / "requirements.txt"
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_path)],
                check=True,
                capture_output=True,
                timeout=300,  # 5 minutes for pip install
            )
        except subprocess.CalledProcessError as e:
            raise BookingServiceError(
                code="PIP_INSTALL_FAILED",
                message=(
                    "Failed to install dependencies: "
                    + (e.stderr.decode() if e.stderr else "unknown error")
                ),
                suggestion="Check requirements.txt and network connectivity",
            )

        # Step 4: Generate systemd service file
        service_template_path = Path("/var/lib/hostkit/templates/booking.service.j2")
        if not service_template_path.exists():
            service_template_path = (
                Path(__file__).parent.parent.parent.parent / "templates" / "booking.service.j2"
            )

        if not service_template_path.exists():
            raise BookingServiceError(
                code="SERVICE_TEMPLATE_NOT_FOUND",
                message="Booking service systemd template not found",
                suggestion="Ensure HostKit is properly installed",
            )

        template = Template(service_template_path.read_text())
        service_content = template.render(
            project_name=project,
            booking_port=booking_port,
            database_url=database_url,
        )

        service_name = f"hostkit-{project}-booking"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_path.write_text(service_content)

        # Step 5: Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "booking.log").touch()
        (log_dir / "booking-error.log").touch()

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(booking_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "booking.log")],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "booking-error.log")],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def _configure_nginx_booking(self, project: str) -> None:
        """Configure Nginx to route /book/* and /admin/* to the booking service."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.add_booking_location(project, self._booking_port(project))

    def _remove_nginx_booking(self, project: str) -> None:
        """Remove the booking location blocks from Nginx config."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.remove_booking_location(project)

    def _start_booking_service(self, project: str) -> None:
        """Start and enable the booking service."""
        service_name = f"hostkit-{project}-booking"

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
            raise BookingServiceError(
                code="SERVICE_START_FAILED",
                message=(
                    "Failed to start booking service: "
                    + (e.stderr.decode() if e.stderr else "unknown error")
                ),
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _stop_booking_service(self, project: str) -> None:
        """Stop and disable the booking service."""
        service_name = f"hostkit-{project}-booking"
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

    def _initialize_booking_config(self, project: str) -> None:
        """Initialize booking_configs table with project configuration."""
        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO booking_configs (project_id, business_name, timezone)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (project_id) DO NOTHING
                    """,
                    [project, project.title().replace("_", " "), "America/New_York"],
                )
        except psycopg2.Error as e:
            raise BookingServiceError(
                code="CONFIG_INIT_FAILED",
                message=f"Failed to initialize booking config: {e}",
                suggestion="Check database connection",
            )
        finally:
            conn.close()

    def booking_is_enabled(self, project: str) -> bool:
        """Check if booking service is enabled for a project."""
        try:
            return self._table_exists(project, "booking_configs")
        except BookingServiceError:
            return False

    def enable_booking(self, project: str) -> dict[str, Any]:
        """Enable booking service for a project.

        Creates:
        - Database tables in project's existing PostgreSQL database
        - FastAPI booking service

        Returns:
            Dictionary with service details and URLs
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise BookingServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.booking_is_enabled(project):
            raise BookingServiceError(
                code="BOOKING_ALREADY_ENABLED",
                message=f"Booking service is already enabled for '{project}'",
                suggestion="Use 'hostkit booking disable' first to reset configuration",
            )

        # Calculate booking port and URLs
        booking_port = self._booking_port(project)
        primary_domain = f"{project}.hostkit.dev"
        api_url = f"https://{primary_domain}/api/booking"
        admin_url = f"https://{primary_domain}/api/admin"
        docs_url = f"https://{primary_domain}/docs"

        try:
            # Step 1: Apply schema to project database
            self._apply_schema(project)

            # Step 2: Verify permissions are correctly applied
            # This prevents the race condition where service starts before grants complete
            self._verify_permissions(project)

            # Step 3: Initialize booking config
            self._initialize_booking_config(project)

            # Step 4: Deploy the FastAPI booking service
            self._deploy_booking_service(project)

            # Step 5: Configure Nginx to route /api/booking/* and /api/admin/* to booking service
            self._configure_nginx_booking(project)

            # Step 6: Start the booking service
            self._start_booking_service(project)

            # Step 7: Regenerate nginx port mappings for wildcard routing
            from hostkit.services.project_service import ProjectService

            ProjectService()._regenerate_nginx_port_mappings()

            return {
                "booking_port": booking_port,
                "api_url": api_url,
                "admin_url": admin_url,
                "docs_url": docs_url,
            }

        except Exception as e:
            # Rollback: clean up all created resources
            try:
                self._stop_booking_service(project)
            except Exception:
                pass
            try:
                self._remove_nginx_booking(project)
            except Exception:
                pass
            try:
                self._drop_schema(project)
            except Exception:
                pass
            try:
                import shutil

                booking_dir = self._booking_dir(project)
                if booking_dir.exists():
                    shutil.rmtree(booking_dir)
            except Exception:
                pass

            if isinstance(e, BookingServiceError):
                raise
            else:
                raise BookingServiceError(
                    code="BOOKING_ENABLE_FAILED",
                    message=f"Failed to enable booking: {e}",
                    suggestion="Check PostgreSQL configuration and ensure project has --with-db",
                )

    def disable_booking(self, project: str, force: bool = False) -> None:
        """Disable booking service for a project.

        Removes:
        - Database tables
        - Booking service files
        - Systemd service

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise BookingServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if booking is enabled
        if not self.booking_is_enabled(project):
            raise BookingServiceError(
                code="BOOKING_NOT_ENABLED",
                message=f"Booking service is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise BookingServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable booking service",
                suggestion=f"Add --force to confirm: 'hostkit booking disable {project} --force'",
            )

        # Step 1: Stop and remove booking service
        self._stop_booking_service(project)

        # Step 2: Remove Nginx booking configuration
        self._remove_nginx_booking(project)

        # Step 3: Remove systemd service file
        service_name = f"hostkit-{project}-booking"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # Step 4: Drop booking tables
        self._drop_schema(project)

        # Step 5: Remove booking directory
        import shutil

        booking_dir = self._booking_dir(project)
        if booking_dir.exists():
            shutil.rmtree(booking_dir)

    def get_booking_status(self, project: str) -> dict[str, Any]:
        """Get booking service status for a project.

        Returns:
            Dictionary with booking configuration and statistics
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise BookingServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        if not self.booking_is_enabled(project):
            return {
                "enabled": False,
                "project": project,
                "booking_port": None,
                "api_url": None,
                "admin_url": None,
                "docs_url": None,
            }

        # Calculate URLs
        booking_port = self._booking_port(project)
        primary_domain = f"{project}.hostkit.dev"
        api_url = f"https://{primary_domain}/api/booking"
        admin_url = f"https://{primary_domain}/api/admin"
        docs_url = f"https://{primary_domain}/docs"

        # Query database for statistics
        conn = self._get_project_db_connection(project)
        provider_count = 0
        service_count = 0
        appointment_count = 0

        try:
            with conn.cursor() as cur:
                # Count providers
                cur.execute("SELECT COUNT(*) FROM providers WHERE is_active = true")
                result = cur.fetchone()
                if result:
                    provider_count = result[0]

                # Count services
                cur.execute("SELECT COUNT(*) FROM services WHERE is_active = true")
                result = cur.fetchone()
                if result:
                    service_count = result[0]

                # Count appointments
                cur.execute("SELECT COUNT(*) FROM appointments WHERE status != 'cancelled'")
                result = cur.fetchone()
                if result:
                    appointment_count = result[0]
        except psycopg2.Error:
            pass  # Ignore query errors
        finally:
            conn.close()

        return {
            "enabled": True,
            "project": project,
            "booking_port": booking_port,
            "api_url": api_url,
            "admin_url": admin_url,
            "docs_url": docs_url,
            "provider_count": provider_count,
            "service_count": service_count,
            "appointment_count": appointment_count,
        }

    def _get_schema_version(self, project: str) -> int:
        """Get the current schema version for a project's booking service."""
        conn = self._get_project_db_connection(project)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'booking_configs' AND column_name = 'schema_version'
                """)
                if not cur.fetchone():
                    return 1
                cur.execute(
                    "SELECT COALESCE(schema_version, 1) FROM booking_configs WHERE project_id = %s",
                    [project],
                )
                result = cur.fetchone()
                return result[0] if result else 1
        finally:
            conn.close()

    def _get_template_version(self) -> int:
        """Get the current template schema version from manifest."""
        import json

        manifest_path = Path("/var/lib/hostkit/templates/booking/migrations/manifest.json")
        if not manifest_path.exists():
            manifest_path = (
                Path(__file__).parent.parent.parent.parent
                / "templates"
                / "booking"
                / "migrations"
                / "manifest.json"
            )
        if not manifest_path.exists():
            return 1
        try:
            manifest = json.loads(manifest_path.read_text())
            return manifest.get("current_version", 1)
        except (json.JSONDecodeError, KeyError):
            return 1

    def _get_pending_migrations(self, project: str) -> list[tuple[int, Path, str]]:
        """Get list of migrations that haven't been applied yet."""
        import json

        current_version = self._get_schema_version(project)
        manifest_path = Path("/var/lib/hostkit/templates/booking/migrations/manifest.json")
        if not manifest_path.exists():
            manifest_path = (
                Path(__file__).parent.parent.parent.parent
                / "templates"
                / "booking"
                / "migrations"
                / "manifest.json"
            )
        if not manifest_path.exists():
            return []
        try:
            manifest = json.loads(manifest_path.read_text())
            migrations = manifest.get("migrations", [])
            pending = []
            for m in migrations:
                if m["version"] > current_version:
                    migrations_dir = manifest_path.parent
                    migration_file = migrations_dir / m["file"]
                    if migration_file.exists():
                        pending.append((m["version"], migration_file, m.get("description", "")))
            return sorted(pending, key=lambda x: x[0])
        except (json.JSONDecodeError, KeyError):
            return []

    def _sync_template_files(self, project: str, dry_run: bool = False) -> list[dict[str, str]]:
        """Sync template files to project's booking directory."""
        import hashlib
        import shutil

        booking_dir = self._booking_dir(project)
        templates_dir = Path("/var/lib/hostkit/templates/booking")
        if not templates_dir.exists():
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "booking"
        if not templates_dir.exists():
            raise BookingServiceError(
                code="TEMPLATES_NOT_FOUND", message="Booking templates not found"
            )
        preserve = {".env", "venv", "__pycache__", ".pyc"}
        changes = []
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file():
                if src_file.name == "schema.sql" or "migrations" in src_file.parts:
                    continue
                rel_path = src_file.relative_to(templates_dir)
                if any(p in str(rel_path) for p in preserve):
                    continue
                dest_file = booking_dir / rel_path
                if not dest_file.exists():
                    action = "new"
                else:
                    src_hash = hashlib.md5(src_file.read_bytes()).hexdigest()
                    dest_hash = hashlib.md5(dest_file.read_bytes()).hexdigest()
                    action = "modified" if src_hash != dest_hash else "unchanged"
                changes.append({"path": str(rel_path), "action": action})
                if not dry_run and action != "unchanged":
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
        return changes

    def _restart_booking_service(self, project: str) -> bool:
        """Restart the booking service."""
        service_name = f"hostkit-{project}-booking"
        try:
            subprocess.run(
                ["systemctl", "restart", f"{service_name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            return True
        except subprocess.SubprocessError:
            return False

    def _health_check(self, project: str, timeout: int = 10) -> bool:
        """Check if booking service is healthy."""
        import urllib.error
        import urllib.request

        port = self._booking_port(project)
        url = f"http://127.0.0.1:{port}/health"
        try:
            req = urllib.request.urlopen(url, timeout=timeout)
            return req.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError):
            return False

    def upgrade_booking(self, project: str, dry_run: bool = False) -> dict[str, Any]:
        """Upgrade booking service to latest version."""
        if not self.booking_is_enabled(project):
            raise BookingServiceError(
                code="BOOKING_NOT_ENABLED", message=f"Booking not enabled for '{project}'"
            )
        current_version = self._get_schema_version(project)
        target_version = self._get_template_version()
        pending_migrations = self._get_pending_migrations(project)
        file_changes = self._sync_template_files(project, dry_run=True)
        files_to_update = [f for f in file_changes if f["action"] != "unchanged"]
        status = self.get_booking_status(project)
        result = {
            "project": project,
            "current_version": current_version,
            "target_version": target_version,
            "dry_run": dry_run,
            "file_changes": files_to_update,
            "files_copied": len([f for f in files_to_update if f["action"] in ("new", "modified")]),
            "migrations": [{"version": v, "description": d} for v, _, d in pending_migrations],
            "migrations_applied": 0,
            "preserved_data": {
                "providers": status.get("provider_count", 0),
                "services": status.get("service_count", 0),
                "appointments": status.get("appointment_count", 0),
            },
            "restarted": False,
            "healthy": False,
        }
        if dry_run:
            result["message"] = "Dry run - no changes applied"
            return result
        try:
            self._sync_template_files(project, dry_run=False)
            conn = self._get_project_db_connection(project)
            try:
                with conn.cursor() as cur:
                    for version, migration_path, _ in pending_migrations:
                        cur.execute(migration_path.read_text())
                        result["migrations_applied"] += 1
                        cur.execute(
                            "UPDATE booking_configs SET schema_version = %s WHERE project_id = %s",
                            [version, project],
                        )
            finally:
                conn.close()
            booking_dir = self._booking_dir(project)
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(booking_dir)], capture_output=True
            )
            result["restarted"] = self._restart_booking_service(project)
            import time

            for _ in range(6):
                time.sleep(2)
                if self._health_check(project):
                    result["healthy"] = True
                    break
            result["message"] = f"Booking service upgraded to v{target_version}"
            return result
        except Exception as e:
            raise BookingServiceError(code="UPGRADE_FAILED", message=f"Failed to upgrade: {e}")

    def seed_demo_data(
        self, project: str, provider_count: int = 3, service_count: int = 5
    ) -> dict[str, Any]:
        """Seed demo data for testing.

        Creates sample providers, services, and rooms.

        Args:
            project: Project name
            provider_count: Number of providers to create
            service_count: Number of services to create

        Returns:
            Dictionary with created counts
        """
        if not self.booking_is_enabled(project):
            raise BookingServiceError(
                code="BOOKING_NOT_ENABLED",
                message=f"Booking service is not enabled for '{project}'",
                suggestion=f"Enable booking first with 'hostkit booking enable {project}'",
            )

        conn = self._get_project_db_connection(project)
        created_providers = 0
        created_services = 0
        created_rooms = 0

        try:
            with conn.cursor() as cur:
                # Get config_id
                cur.execute("SELECT id FROM booking_configs WHERE project_id = %s", [project])
                result = cur.fetchone()
                if not result:
                    raise BookingServiceError(
                        code="CONFIG_NOT_FOUND",
                        message="Booking config not found",
                        suggestion="This should not happen - please report as a bug",
                    )
                config_id = result[0]

                # Create providers
                provider_names = ["Sarah", "Mike", "Lisa", "John", "Emma", "Alex", "Maria", "David"]
                for i in range(min(provider_count, len(provider_names))):
                    cur.execute(
                        """
                        INSERT INTO providers (config_id, name, email, bio, is_active, sort_order)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        [
                            config_id,
                            provider_names[i],
                            f"{provider_names[i].lower()}@example.com",
                            "Licensed professional with 5+ years of experience.",
                            True,
                            i,
                        ],
                    )
                    created_providers += 1

                # Create rooms
                room_names = ["Treatment Room A", "Treatment Room B", "Consultation Room"]
                for i, room_name in enumerate(room_names):
                    cur.execute(
                        """
                        INSERT INTO rooms (config_id, name, type, is_active, sort_order)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            config_id,
                            room_name,
                            "treatment" if "Treatment" in room_name else "consultation",
                            True,
                            i,
                        ],
                    )
                    created_rooms += 1

                # Create services and collect IDs
                services = [
                    ("30 Minute Session", "Quick 30-minute session", 30, 6000, "massage"),
                    ("60 Minute Session", "Standard 60-minute session", 60, 11000, "massage"),
                    ("90 Minute Session", "Extended 90-minute session", 90, 15000, "massage"),
                    ("2 Hour Session", "Full 2-hour session", 120, 19000, "massage"),
                    ("Consultation", "Initial consultation", 30, 5000, "consultation"),
                ]
                service_ids = []
                for i in range(min(service_count, len(services))):
                    name, desc, duration, price, category = services[i]
                    cur.execute(
                        """
                        INSERT INTO services (
                            config_id, name, description,
                            duration_minutes, price_cents,
                            category, is_active, sort_order
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        [config_id, name, desc, duration, price, category, True, i],
                    )
                    service_id = cur.fetchone()[0]
                    service_ids.append(service_id)
                    created_services += 1

                # Link all providers to all services (so any provider can perform any service)
                cur.execute("SELECT id FROM providers WHERE config_id = %s", [config_id])
                provider_ids = [row[0] for row in cur.fetchall()]

                for provider_id in provider_ids:
                    for service_id in service_ids:
                        cur.execute(
                            """
                            INSERT INTO provider_services (provider_id, service_id)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            [provider_id, service_id],
                        )

                # Also create provider schedules (Mon-Fri 9am-5pm)
                for provider_id in provider_ids:
                    for day in range(5):  # Monday (0) to Friday (4)
                        cur.execute(
                            """
                            INSERT INTO provider_schedules (
                                provider_id, day_of_week,
                                start_time, end_time, is_active
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            [provider_id, day, "09:00", "17:00", True],
                        )

        except psycopg2.Error as e:
            raise BookingServiceError(
                code="SEED_FAILED",
                message=f"Failed to seed demo data: {e}",
                suggestion="Check database connection",
            )
        finally:
            conn.close()

        return {
            "providers_created": created_providers,
            "services_created": created_services,
            "rooms_created": created_rooms,
        }

"""Payment service management for HostKit.

Provides per-project payment processing via Stripe Connect Express accounts.
"""

import os
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


# Register payment service with capabilities registry
CapabilitiesRegistry.register_service(ServiceMeta(
    name="payments",
    description="Stripe Connect payment processing (one-time, subscriptions, refunds)",
    provision_flag="--with-payments",
    enable_command="hostkit payments enable {project}",
    env_vars_provided=["PAYMENT_URL", "STRIPE_ACCOUNT_ID"],
    related_commands=["payments enable", "payments disable", "payments status", "payments logs"],
))


@dataclass
class PaymentDatabaseCredentials:
    """Payment database connection credentials."""

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


class PaymentServiceError(Exception):
    """Base exception for payment service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure password."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class PaymentService:
    """Service for managing per-project payment services."""

    def __init__(self) -> None:
        """Initialize the payment service."""
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        # Load Stripe keys from /etc/hostkit/stripe.ini
        self._stripe_secret_key = self._load_stripe_key()

    def _load_stripe_key(self) -> str:
        """Load Stripe secret key from /etc/hostkit/stripe.ini."""
        stripe_ini_path = Path("/etc/hostkit/stripe.ini")
        if not stripe_ini_path.exists():
            return ""  # Return empty, will error on enable if not configured

        try:
            import configparser
            config = configparser.ConfigParser()
            config.read(stripe_ini_path)
            return config.get("stripe", "secret_key", fallback="")
        except Exception:
            return ""

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
            raise PaymentServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL: {e}",
                suggestion="Check PostgreSQL is running and credentials are correct",
            )

    def _payment_db_name(self, project: str) -> str:
        """Generate payment database name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_payment_db"

    def _payment_role_name(self, project: str) -> str:
        """Generate payment database role name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_payment_user"

    def _payment_port(self, project: str) -> int:
        """Calculate payment service port from project port.

        Payment service runs on project_port + 2000.
        (Auth uses +1000, so payment is +2000)
        """
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise PaymentServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return project_data["port"] + 2000

    def _payment_dir(self, project: str) -> Path:
        """Get the payment service directory for a project."""
        return Path(f"/home/{project}/.payment")

    def _validate_identifier(self, name: str) -> None:
        """Validate a PostgreSQL identifier to prevent SQL injection.

        Accepts project names with hyphens (they get converted to underscores
        for PostgreSQL identifiers).
        """
        import re

        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = name.replace("-", "_")
        if not re.match(r"^[a-z][a-z0-9_]{0,62}$", safe_name):
            raise PaymentServiceError(
                code="INVALID_IDENTIFIER",
                message=f"Invalid identifier: {name}",
                suggestion="Use lowercase letters, numbers, hyphens, and underscores only",
            )

    def _database_exists(self, project: str) -> bool:
        """Check if payment database exists for a project."""
        db_name = self._payment_db_name(project)

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", [db_name]
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _create_payment_database(self, project: str) -> PaymentDatabaseCredentials:
        """Create a PostgreSQL database for the payment service.

        Creates:
        - Database: {project}_payment_db
        - Role: {project}_payment_user with random password
        """
        db_name = self._payment_db_name(project)
        role_name = self._payment_role_name(project)

        # Validate identifiers
        self._validate_identifier(project)

        # Check if database already exists
        if self._database_exists(project):
            raise PaymentServiceError(
                code="PAYMENT_DATABASE_EXISTS",
                message=f"Payment database '{db_name}' already exists",
                suggestion="Disable payments first with 'hostkit payments disable' or use a different project",
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

        return PaymentDatabaseCredentials(
            database=db_name,
            username=role_name,
            password=password,
            host=self.config.postgres_host,
            port=self.config.postgres_port,
        )

    def _apply_schema(self, project: str, credentials: PaymentDatabaseCredentials) -> None:
        """Apply the payment database schema to a project's payment database."""
        # Read schema from templates
        schema_path = Path("/var/lib/hostkit/templates/payment/schema.sql")
        if not schema_path.exists():
            # Try dev path
            schema_path = Path(__file__).parent.parent.parent.parent / "templates" / "payment" / "schema.sql"

        if not schema_path.exists():
            raise PaymentServiceError(
                code="SCHEMA_NOT_FOUND",
                message="Payment schema.sql not found",
                suggestion="Ensure HostKit is properly installed",
            )

        schema_sql = schema_path.read_text()

        try:
            # Connect to the payment database as the payment user
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
            raise PaymentServiceError(
                code="SCHEMA_APPLY_FAILED",
                message=f"Failed to apply payment schema: {e}",
                suggestion="Check database connection and permissions",
            )

    def _create_stripe_express_account(self, project: str) -> dict[str, Any]:
        """Create a Stripe Express account for the project.

        Returns:
            Dict with stripe_account_id and onboarding_url
        """
        if not self._stripe_secret_key:
            raise PaymentServiceError(
                code="STRIPE_NOT_CONFIGURED",
                message="Stripe secret key not configured",
                suggestion="Add Stripe keys to /etc/hostkit/stripe.ini",
            )

        try:
            import stripe
            stripe.api_key = self._stripe_secret_key

            # Create Express account
            account = stripe.Account.create(
                type="express",
                capabilities={
                    "card_payments": {"requested": True},
                    "transfers": {"requested": True},
                },
                settings={
                    "payouts": {
                        "schedule": {
                            "interval": "daily",
                            "delay_days": 2,  # Stripe default
                        }
                    }
                },
                metadata={
                    "hostkit_project": project,
                },
            )

            # Create onboarding link
            account_link = stripe.AccountLink.create(
                account=account.id,
                refresh_url=f"https://{project}.hostkit.dev/payments/onboarding/refresh",
                return_url=f"https://{project}.hostkit.dev/payments/onboarding/complete",
                type="account_onboarding",
            )

            return {
                "stripe_account_id": account.id,
                "onboarding_url": account_link.url,
            }

        except Exception as e:
            raise PaymentServiceError(
                code="STRIPE_ACCOUNT_CREATE_FAILED",
                message=f"Failed to create Stripe account: {e}",
                suggestion="Check Stripe API key and network connectivity",
            )

    def _deploy_payment_service(
        self, project: str, credentials: PaymentDatabaseCredentials, stripe_account_id: str
    ) -> None:
        """Deploy the FastAPI payment service for a project.

        Steps:
        1. Copy payment service template files to /home/{project}/.payment/
        2. Create Python virtual environment
        3. Install dependencies
        4. Generate systemd service file
        5. Create log files
        """
        import shutil
        from jinja2 import Template

        payment_dir = self._payment_dir(project)
        templates_dir = Path("/var/lib/hostkit/templates/payment")
        if not templates_dir.exists():
            # Try dev path
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "payment"

        if not templates_dir.exists():
            raise PaymentServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Payment service templates not found",
                suggestion="Ensure HostKit is properly installed",
            )

        # Step 1: Copy template files
        if payment_dir.exists():
            shutil.rmtree(payment_dir)

        payment_dir.mkdir(parents=True, exist_ok=True)

        # Get template context for rendering config.py
        payment_port = self._payment_port(project)
        primary_domain = f"{project}.hostkit.dev"
        base_url = f"https://{primary_domain}"

        template_context = {
            "project": project,
            "payment_port": payment_port,
            "domain": primary_domain,
            "base_url": base_url,
            "stripe_account_id": stripe_account_id,
        }

        # Copy all template files (render config.py.j2 as Jinja2 template)
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file() and src_file.name != "schema.sql":
                rel_path = src_file.relative_to(templates_dir)
                dest_file = payment_dir / rel_path

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
        venv_path = payment_dir / "venv"
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise PaymentServiceError(
                code="VENV_CREATE_FAILED",
                message=f"Failed to create virtual environment: {e.stderr.decode() if e.stderr else 'unknown error'}",
                suggestion="Ensure python3-venv is installed",
            )

        # Step 3: Install dependencies
        pip_path = venv_path / "bin" / "pip"
        requirements_path = payment_dir / "requirements.txt"
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_path)],
                check=True,
                capture_output=True,
                timeout=300,  # 5 minutes for pip install
            )
        except subprocess.CalledProcessError as e:
            raise PaymentServiceError(
                code="PIP_INSTALL_FAILED",
                message=f"Failed to install dependencies: {e.stderr.decode() if e.stderr else 'unknown error'}",
                suggestion="Check requirements.txt and network connectivity",
            )

        # Step 4: Generate systemd service file
        service_template_path = Path("/var/lib/hostkit/templates/payment.service.j2")
        if not service_template_path.exists():
            service_template_path = Path(__file__).parent.parent.parent.parent / "templates" / "payment.service.j2"

        if not service_template_path.exists():
            raise PaymentServiceError(
                code="SERVICE_TEMPLATE_NOT_FOUND",
                message="Payment service systemd template not found",
                suggestion="Ensure HostKit is properly installed",
            )

        template = Template(service_template_path.read_text())
        service_content = template.render(
            project_name=project,
            payment_port=payment_port,
            payment_db_url=credentials.connection_url,
            stripe_account_id=stripe_account_id,
            stripe_secret_key=self._stripe_secret_key,
        )

        service_name = f"hostkit-{project}-payment"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_path.write_text(service_content)

        # Step 5: Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "payment.log").touch()
        (log_dir / "payment-error.log").touch()

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(payment_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "payment.log")],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "payment-error.log")],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def _configure_nginx_payment(self, project: str) -> None:
        """Configure Nginx to route /payments/* to the payment service.

        This adds a location block to the project's Nginx config.
        """
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.add_payment_location(project, self._payment_port(project))

    def _add_payment_env_vars(self, project: str, stripe_account_id: str) -> None:
        """Add payment environment variables to the project's .env file."""
        env_path = Path(f"/home/{project}/.env")
        payments_url = f"https://{project}.hostkit.dev/payments"

        # Read existing content
        existing = ""
        if env_path.exists():
            existing = env_path.read_text()

        # Remove any existing STRIPE_ACCOUNT_ID or PAYMENTS_URL lines
        lines = [
            line for line in existing.splitlines()
            if not line.startswith("STRIPE_ACCOUNT_ID=") and not line.startswith("PAYMENTS_URL=")
        ]

        # Add new values
        lines.append(f"STRIPE_ACCOUNT_ID={stripe_account_id}")
        lines.append(f"PAYMENTS_URL={payments_url}")

        # Write back
        env_path.write_text("\n".join(lines) + "\n")

        # Fix ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

    def _remove_payment_env_vars(self, project: str) -> None:
        """Remove payment environment variables from the project's .env file."""
        env_path = Path(f"/home/{project}/.env")

        if not env_path.exists():
            return

        # Read and filter out payment env vars
        existing = env_path.read_text()
        lines = [
            line for line in existing.splitlines()
            if not line.startswith("STRIPE_ACCOUNT_ID=") and not line.startswith("PAYMENTS_URL=")
        ]

        # Write back
        env_path.write_text("\n".join(lines) + "\n")

    def _remove_nginx_payment(self, project: str) -> None:
        """Remove the /payments/* location block from Nginx config."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.remove_payment_location(project)

    def _start_payment_service(self, project: str) -> None:
        """Start and enable the payment service."""
        service_name = f"hostkit-{project}-payment"

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
            raise PaymentServiceError(
                code="SERVICE_START_FAILED",
                message=f"Failed to start payment service: {e.stderr.decode() if e.stderr else 'unknown error'}",
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _stop_payment_service(self, project: str) -> None:
        """Stop and disable the payment service."""
        service_name = f"hostkit-{project}-payment"
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

    def _delete_payment_database(self, project: str) -> None:
        """Delete the payment database and role for a project."""
        db_name = self._payment_db_name(project)
        role_name = self._payment_role_name(project)

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
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name))
                )

                # Drop role
                cur.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )

        finally:
            conn.close()

    def payment_is_enabled(self, project: str) -> bool:
        """Check if payment service is enabled for a project."""
        return self._database_exists(project)

    def enable_payments(self, project: str) -> dict[str, Any]:
        """Enable payment service for a project.

        Creates:
        - PostgreSQL database: {project}_payment_db
        - Database role: {project}_payment_user
        - Stripe Express account
        - FastAPI payment service

        Returns:
            Dictionary with stripe_account_id, onboarding_url, and service details
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise PaymentServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.payment_is_enabled(project):
            raise PaymentServiceError(
                code="PAYMENT_ALREADY_ENABLED",
                message=f"Payment service is already enabled for '{project}'",
                suggestion="Use 'hostkit payments disable' first to reset configuration",
            )

        # Calculate payment port
        payment_port = self._payment_port(project)

        try:
            # Step 1: Create Stripe Express account
            stripe_result = self._create_stripe_express_account(project)

            # Step 2: Create payment database
            credentials = self._create_payment_database(project)

            # Step 3: Apply schema to payment database
            self._apply_schema(project, credentials)

            # Step 4: Deploy the FastAPI payment service
            self._deploy_payment_service(project, credentials, stripe_result["stripe_account_id"])

            # Step 5: Configure Nginx to route /payments/* to payment service
            self._configure_nginx_payment(project)

            # Step 6: Start the payment service
            self._start_payment_service(project)

            # Step 7: Regenerate nginx port mappings for wildcard routing
            from hostkit.services.project_service import ProjectService
            ProjectService()._regenerate_nginx_port_mappings()

            # Step 8: Add env vars to project .env
            self._add_payment_env_vars(project, stripe_result["stripe_account_id"])

            return {
                "stripe_account_id": stripe_result["stripe_account_id"],
                "onboarding_url": stripe_result["onboarding_url"],
                "payment_port": payment_port,
                "payment_db": credentials.database,
                "payment_db_user": credentials.username,
            }

        except Exception as e:
            # Rollback: clean up all created resources
            try:
                self._stop_payment_service(project)
            except Exception:
                pass
            try:
                self._remove_nginx_payment(project)
            except Exception:
                pass
            try:
                self._delete_payment_database(project)
            except Exception:
                pass
            try:
                import shutil
                payment_dir = self._payment_dir(project)
                if payment_dir.exists():
                    shutil.rmtree(payment_dir)
            except Exception:
                pass

            if isinstance(e, PaymentServiceError):
                raise
            else:
                raise PaymentServiceError(
                    code="PAYMENT_ENABLE_FAILED",
                    message=f"Failed to enable payments: {e}",
                    suggestion="Check PostgreSQL and Stripe configuration",
                )

    def disable_payments(self, project: str, force: bool = False) -> None:
        """Disable payment service for a project.

        Removes:
        - PostgreSQL database and role
        - Payment service files
        - Systemd service

        Note: Stripe account must be closed manually in Stripe dashboard.

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise PaymentServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if payment is enabled
        if not self.payment_is_enabled(project):
            raise PaymentServiceError(
                code="PAYMENT_NOT_ENABLED",
                message=f"Payment service is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise PaymentServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable payment service",
                suggestion=f"Add --force to confirm: 'hostkit payments disable {project} --force'",
            )

        # Step 1: Stop and remove payment service
        self._stop_payment_service(project)

        # Step 2: Remove Nginx payment configuration
        self._remove_nginx_payment(project)

        # Step 3: Remove systemd service file
        service_name = f"hostkit-{project}-payment"
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # Step 4: Delete payment database and role
        self._delete_payment_database(project)

        # Step 5: Remove payment directory
        import shutil
        payment_dir = self._payment_dir(project)
        if payment_dir.exists():
            shutil.rmtree(payment_dir)

        # Step 6: Remove env vars from project .env
        self._remove_payment_env_vars(project)

    def get_payment_status(self, project: str) -> dict[str, Any]:
        """Get payment service status for a project.

        Returns:
            Dictionary with payment configuration and Stripe account status
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise PaymentServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        if not self.payment_is_enabled(project):
            return {
                "enabled": False,
                "project": project,
                "account_status": None,
                "stripe_account_id": None,
                "payment_port": None,
                "payment_db": None,
            }

        # Read configuration from payment service config.py
        payment_dir = self._payment_dir(project)
        config_path = payment_dir / "config.py"

        stripe_account_id = None
        if config_path.exists():
            import re
            content = config_path.read_text()
            match = re.search(r'stripe_account_id:\s*str\s*=\s*"([^"]+)"', content)
            if match:
                stripe_account_id = match.group(1)

        # Query Stripe for account status
        account_status = "unknown"
        charges_enabled = False
        payouts_enabled = False
        currency = "usd"
        onboarding_url = None

        if stripe_account_id and self._stripe_secret_key:
            try:
                import stripe
                stripe.api_key = self._stripe_secret_key

                account = stripe.Account.retrieve(stripe_account_id)
                charges_enabled = account.charges_enabled
                payouts_enabled = account.payouts_enabled
                currency = account.default_currency or "usd"

                if not charges_enabled or not payouts_enabled:
                    account_status = "pending"
                    # Generate new onboarding link
                    account_link = stripe.AccountLink.create(
                        account=stripe_account_id,
                        refresh_url=f"https://{project}.hostkit.dev/payments/onboarding/refresh",
                        return_url=f"https://{project}.hostkit.dev/payments/onboarding/complete",
                        type="account_onboarding",
                    )
                    onboarding_url = account_link.url
                else:
                    account_status = "active"

            except Exception:
                account_status = "error"

        return {
            "enabled": True,
            "project": project,
            "account_status": account_status,
            "stripe_account_id": stripe_account_id,
            "payment_port": self._payment_port(project),
            "payment_db": self._payment_db_name(project),
            "currency": currency,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
            "onboarding_url": onboarding_url,
        }

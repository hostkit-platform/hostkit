"""Full provisioning service for HostKit projects.

Provides one-command project provisioning with all supporting services.
"""

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hostkit.database import get_db
from hostkit.services.deploy_service import DeployService, DeployServiceError
from hostkit.services.health_service import HealthService, HealthServiceError
from hostkit.services.nginx_service import NginxError, NginxService
from hostkit.services.project_service import ProjectService, ProjectServiceError
from hostkit.services.ssl_service import SSLError, SSLService


@dataclass
class ProvisionResult:
    """Result of a provisioning operation."""

    project: str
    runtime: str
    port: int
    success: bool
    steps_completed: list[str] = field(default_factory=list)
    steps_failed: list[str] = field(default_factory=list)
    database_created: bool = False
    database_name: str | None = None
    auth_enabled: bool = False
    auth_port: int | None = None
    secrets_injected: bool = False
    secrets_count: int = 0
    ssh_keys_added: int = 0
    ssh_keys_failed: list[str] = field(default_factory=list)
    domain_configured: str | None = None
    ssl_provisioned: bool = False
    deployed: bool = False
    release_name: str | None = None
    service_started: bool = False
    health_status: str | None = None
    error: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "project": self.project,
            "runtime": self.runtime,
            "port": self.port,
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "database_created": self.database_created,
            "database_name": self.database_name,
            "auth_enabled": self.auth_enabled,
            "auth_port": self.auth_port,
            "secrets_injected": self.secrets_injected,
            "secrets_count": self.secrets_count,
            "ssh_keys_added": self.ssh_keys_added,
            "ssh_keys_failed": self.ssh_keys_failed,
            "domain_configured": self.domain_configured,
            "ssl_provisioned": self.ssl_provisioned,
            "deployed": self.deployed,
            "release_name": self.release_name,
            "service_started": self.service_started,
            "health_status": self.health_status,
            "error": self.error,
            "suggestion": self.suggestion,
        }


class ProvisionServiceError(Exception):
    """Base exception for provision service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class ProvisionService:
    """Service for one-command project provisioning."""

    def __init__(self) -> None:
        self.db = get_db()
        self.project_service = ProjectService()
        self.deploy_service = DeployService()
        self.nginx_service = NginxService()
        self.ssl_service = SSLService()
        self.health_service = HealthService()

    def provision(
        self,
        name: str,
        runtime: str = "python",
        with_db: bool = False,
        with_auth: bool = False,
        with_secrets: bool = False,
        ssh_keys: list[str] | None = None,
        github_users: list[str] | None = None,
        domain: str | None = None,
        ssl: bool = False,
        ssl_email: str | None = None,
        source: Path | None = None,
        install_deps: bool = True,
        start: bool = True,
    ) -> ProvisionResult:
        """Provision a complete project with all supporting services.

        Steps:
        1. Create project
        2. Create database (if with_db)
        3. Enable auth (if with_auth)
        4. Inject secrets (if with_secrets)
        5. Add SSH keys (if ssh_keys or github_users)
        6. Add domain to nginx (if domain)
        7. Provision SSL (if ssl and domain)
        8. Deploy code (if source)
        9. Start service (if start)
        10. Health check

        Args:
            name: Project name
            runtime: Runtime type (python, node, nextjs, static)
            with_db: Create a PostgreSQL database
            with_auth: Enable authentication service
            with_secrets: Inject secrets from vault into .env
            ssh_keys: List of SSH public keys to add for project user access
            github_users: List of GitHub usernames to fetch SSH keys from
            domain: Domain to configure in Nginx
            ssl: Provision SSL certificate (requires domain)
            ssl_email: Admin email for Let's Encrypt registration
            source: Source directory to deploy
            install_deps: Install dependencies during deploy
            start: Start the service after provisioning

        Returns:
            ProvisionResult with all step outcomes
        """
        result = ProvisionResult(
            project=name,
            runtime=runtime,
            port=0,
            success=False,
        )

        # Step 1: Create project
        try:
            project_info = self.project_service.create_project(
                name=name,
                runtime=runtime,
            )
            result.port = project_info.port
            result.steps_completed.append("project_create")
        except ProjectServiceError as e:
            result.error = f"Failed to create project: {e.message}"
            result.suggestion = e.suggestion
            result.steps_failed.append("project_create")
            return result
        except Exception as e:
            result.error = f"Failed to create project: {e}"
            result.steps_failed.append("project_create")
            return result

        # Step 2: Create database (if requested)
        if with_db:
            try:
                db_result = self._create_database(name)
                result.database_created = True
                result.database_name = db_result["database"]
                result.steps_completed.append("db_create")
            except Exception as e:
                result.database_created = False
                result.steps_failed.append("db_create")
                result.error = f"Failed to create database: {e}"
                # Continue with other steps

        # Step 3: Enable auth (if requested)
        if with_auth:
            try:
                auth_result = self._enable_auth(name)
                result.auth_enabled = True
                result.auth_port = auth_result.get("auth_port")
                result.steps_completed.append("auth_enable")
            except Exception as e:
                result.auth_enabled = False
                result.steps_failed.append("auth_enable")
                result.error = f"Failed to enable auth: {e}"
                # Continue with other steps

        # Step 4: Inject secrets (if requested)
        if with_secrets:
            try:
                secrets_result = self._inject_secrets(name)
                result.secrets_injected = True
                result.secrets_count = secrets_result.get("total_injected", 0)
                result.steps_completed.append("secrets_inject")
            except Exception as e:
                result.secrets_injected = False
                result.steps_failed.append("secrets_inject")
                result.error = f"Failed to inject secrets: {e}"
                # Continue with other steps

        # Step 5: Add SSH keys (if provided)
        if ssh_keys or github_users:
            try:
                keys_added, keys_failed = self._add_ssh_keys(
                    name, ssh_keys or [], github_users or []
                )
                result.ssh_keys_added = keys_added
                result.ssh_keys_failed = keys_failed
                if keys_added > 0:
                    result.steps_completed.append("ssh_keys")
                if keys_failed:
                    result.steps_failed.append("ssh_keys_partial")
                    result.error = f"Some SSH keys failed: {', '.join(keys_failed)}"
            except Exception as e:
                result.steps_failed.append("ssh_keys")
                result.error = f"Failed to add SSH keys: {e}"
                # Continue with other steps

        # Step 6: Add domain to nginx (if provided)
        if domain:
            try:
                self.nginx_service.add_domain(name, domain)
                result.domain_configured = domain
                result.steps_completed.append("nginx_add")
            except NginxError as e:
                result.steps_failed.append("nginx_add")
                result.error = f"Failed to configure domain: {e.message}"
                # Continue with other steps
            except Exception as e:
                result.steps_failed.append("nginx_add")
                result.error = f"Failed to configure domain: {e}"

        # Step 7: Provision SSL (if requested and domain configured)
        if ssl and result.domain_configured:
            try:
                self.ssl_service.provision(result.domain_configured, email=ssl_email)
                result.ssl_provisioned = True
                result.steps_completed.append("ssl_provision")
            except SSLError as e:
                result.ssl_provisioned = False
                result.steps_failed.append("ssl_provision")
                result.error = f"Failed to provision SSL: {e.message}"
                # Continue with other steps
            except Exception as e:
                result.ssl_provisioned = False
                result.steps_failed.append("ssl_provision")
                result.error = f"Failed to provision SSL: {e}"

        # Step 8: Deploy code (if source provided)
        if source:
            try:
                deploy_result = self.deploy_service.deploy(
                    project=name,
                    source=source,
                    install_deps=install_deps,
                    restart=False,  # Don't restart yet, we'll start later
                )
                result.deployed = True
                if deploy_result.release:
                    result.release_name = deploy_result.release.release_name
                result.steps_completed.append("deploy")
            except DeployServiceError as e:
                result.deployed = False
                result.steps_failed.append("deploy")
                result.error = f"Failed to deploy: {e.message}"
            except Exception as e:
                result.deployed = False
                result.steps_failed.append("deploy")
                result.error = f"Failed to deploy: {e}"

        # Step 9: Start service (if requested)
        if start and runtime != "static":
            try:
                subprocess.run(
                    ["systemctl", "start", f"hostkit-{name}"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["systemctl", "enable", f"hostkit-{name}"],
                    check=True,
                    capture_output=True,
                )
                result.service_started = True
                result.steps_completed.append("service_start")
            except subprocess.CalledProcessError as e:
                result.service_started = False
                result.steps_failed.append("service_start")
                error_msg = e.stderr.decode() if e.stderr else "unknown error"
                result.error = f"Failed to start service: {error_msg}"
            except Exception as e:
                result.service_started = False
                result.steps_failed.append("service_start")
                result.error = f"Failed to start service: {e}"

        # Step 10: Health check (if service was started)
        if result.service_started:
            try:
                # Give the service a moment to start
                time.sleep(2)
                health = self.health_service.check_health(name)
                result.health_status = health.overall
                result.steps_completed.append("health_check")
            except HealthServiceError:
                result.health_status = "unknown"
                result.steps_failed.append("health_check")
            except Exception:
                result.health_status = "unknown"
                result.steps_failed.append("health_check")

        # Determine overall success
        # Critical failures: project_create, deploy (if source provided)
        critical_failures = {"project_create"}
        if source:
            critical_failures.add("deploy")

        result.success = not any(step in result.steps_failed for step in critical_failures)

        # Generate suggestion if there were failures
        if result.steps_failed:
            failed_steps = ", ".join(result.steps_failed)
            result.suggestion = (
                f"Some steps failed: {failed_steps}. Review logs and retry individual steps."
            )

        return result

    def _create_database(self, project: str) -> dict[str, Any]:
        """Create a PostgreSQL database for the project.

        Uses the db service to create a database and save credentials to .env.
        """
        from hostkit.services.database_service import DatabaseService, DatabaseServiceError

        db_service = DatabaseService()

        try:
            credentials = db_service.create_database(project)
            # Save DATABASE_URL to the project's .env file
            db_service.update_project_env(project, credentials)
            return {
                "database": credentials.database,
                "username": credentials.username,
                "host": credentials.host,
                "port": credentials.port,
                "connection_url": credentials.connection_url,
            }
        except DatabaseServiceError:
            raise
        except Exception as e:
            raise ProvisionServiceError(
                code="DB_CREATE_FAILED",
                message=f"Failed to create database: {e}",
                suggestion="Check PostgreSQL is running and has sufficient permissions",
            )

    def _enable_auth(self, project: str) -> dict[str, Any]:
        """Enable authentication service for the project."""
        from hostkit.services.auth_service import AuthService, AuthServiceError

        auth_service = AuthService()

        try:
            config = auth_service.enable_auth(project)
            return {
                "enabled": True,
                "auth_port": config.port,
                "auth_db": config.auth_db,
            }
        except AuthServiceError:
            raise
        except Exception as e:
            raise ProvisionServiceError(
                code="AUTH_ENABLE_FAILED",
                message=f"Failed to enable auth: {e}",
                suggestion="Check PostgreSQL is running and templates are installed",
            )

    def _inject_secrets(self, project: str) -> dict[str, Any]:
        """Inject secrets from the vault into the project's .env file."""
        from hostkit.services.secrets_service import SecretsService, SecretsServiceError

        secrets_service = SecretsService()

        try:
            result = secrets_service.inject_secrets(project)
            return result
        except SecretsServiceError:
            raise
        except Exception as e:
            raise ProvisionServiceError(
                code="SECRETS_INJECT_FAILED",
                message=f"Failed to inject secrets: {e}",
                suggestion="Ensure secrets are defined and master key exists",
            )

    def _add_ssh_keys(
        self, project: str, ssh_keys: list[str], github_users: list[str]
    ) -> tuple[int, list[str]]:
        """Add SSH keys for project user access.

        Automatically enables SSH for the project when keys are provided.

        Args:
            project: Project name
            ssh_keys: List of SSH public key strings
            github_users: List of GitHub usernames to fetch keys from

        Returns:
            Tuple of (keys_added_count, list_of_failed_items)
        """
        from hostkit.services import ssh_service

        keys_added = 0
        failed: list[str] = []

        # Enable SSH for the project first (so the project user can SSH in)
        try:
            ssh_service.enable(project)
        except Exception as e:
            failed.append(f"ssh_enable ({e})")

        # Collect all keys to add
        all_keys: list[tuple[str, str]] = []  # (key, source_label)

        # Add direct SSH keys
        for key in ssh_keys:
            all_keys.append((key, f"key:{key[:20]}..."))

        # Fetch keys from GitHub users
        for username in github_users:
            try:
                github_keys = ssh_service.fetch_github_keys(username)
                for key in github_keys:
                    all_keys.append((key, f"github:{username}"))
            except ValueError as e:
                failed.append(f"github:{username} ({e})")
            except Exception as e:
                failed.append(f"github:{username} (fetch failed: {e})")

        # Add each key
        for key, source in all_keys:
            try:
                ssh_service.add_key(project, key)
                keys_added += 1
            except ValueError as e:
                # Key already exists or invalid
                failed.append(f"{source} ({e})")
            except Exception as e:
                failed.append(f"{source} (add failed: {e})")

        return keys_added, failed

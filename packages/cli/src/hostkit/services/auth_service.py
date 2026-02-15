"""Authentication service management for HostKit.

Provides per-project authentication services with OAuth, email/password,
magic links, and anonymous session support.
"""

import os
import secrets
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jinja2 import Template
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register auth service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="auth",
        description=("HostKit Auth service (OAuth, magic links, email/password)"),
        provision_flag="--with-auth",
        enable_command="hostkit auth enable {project}",
        env_vars_provided=["AUTH_URL", "AUTH_JWT_PUBLIC_KEY"],
        related_commands=["auth enable", "auth disable", "auth config", "auth logs"],
    )
)


# Auth database schema SQL
AUTH_SCHEMA_SQL = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE,
    email_verified BOOLEAN DEFAULT FALSE,
    password_hash VARCHAR(255),
    is_anonymous BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_sign_in_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- OAuth accounts (for linking multiple providers)
CREATE TABLE IF NOT EXISTS oauth_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    provider_user_id VARCHAR(255) NOT NULL,
    provider_email VARCHAR(255),
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider, provider_user_id)
);

-- Sessions (refresh tokens)
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash VARCHAR(255) UNIQUE NOT NULL,
    device_info JSONB DEFAULT '{}'::jsonb,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

-- Magic links
CREATE TABLE IF NOT EXISTS magic_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);

-- Email verification tokens
CREATE TABLE IF NOT EXISTS email_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    verified_at TIMESTAMPTZ
);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_resets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user_id ON oauth_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_accounts_provider
    ON oauth_accounts(provider, provider_user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_refresh_token ON sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_magic_links_token ON magic_links(token_hash);
CREATE INDEX IF NOT EXISTS idx_magic_links_expires_at ON magic_links(expires_at);
CREATE INDEX IF NOT EXISTS idx_email_verifications_token ON email_verifications(token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_user_id ON password_resets(user_id);
"""


@dataclass
class AuthDatabaseCredentials:
    """Auth database connection credentials."""

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


@dataclass
class AuthConfig:
    """Authentication configuration for a project."""

    project: str
    enabled: bool
    port: int
    auth_db: str
    auth_db_user: str
    jwt_public_key_path: str
    jwt_private_key_path: str
    google_client_id: str | None = None  # Native iOS/Android client ID
    google_web_client_id: str | None = None  # Web OAuth client ID
    google_client_secret: str | None = None
    apple_client_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    email_enabled: bool = True
    magic_link_enabled: bool = True
    anonymous_enabled: bool = True
    access_token_expire_minutes: int = 60  # 1 hour
    refresh_token_expire_days: int = 30
    created_at: str | None = None


class AuthServiceError(Exception):
    """Base exception for auth service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_secure_token(length: int = 64) -> str:
    """Generate a cryptographically secure token for magic links etc."""
    return secrets.token_urlsafe(length)


class AuthService:
    """Service for managing per-project authentication services."""

    def __init__(self) -> None:
        """Initialize the auth service."""
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
            raise AuthServiceError(
                code="PG_CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL: {e}",
                suggestion="Check PostgreSQL is running and credentials are correct",
            )

    def _auth_db_name(self, project: str) -> str:
        """Generate auth database name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_auth_db"

    def _auth_role_name(self, project: str) -> str:
        """Generate auth database role name for a project."""
        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = project.replace("-", "_")
        return f"{safe_name}_auth_user"

    def _auth_port(self, project: str) -> int:
        """Calculate auth service port from project port.

        Auth service runs on project_port + 1000.
        """
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
        return project_data["port"] + 1000

    def _auth_dir(self, project: str) -> Path:
        """Get the auth service directory for a project."""
        return Path(f"/home/{project}/.auth")

    def _validate_identifier(self, name: str) -> None:
        """Validate a PostgreSQL identifier to prevent SQL injection.

        Accepts project names with hyphens (they get converted to underscores
        for PostgreSQL identifiers).
        """
        import re

        # Convert hyphens to underscores for PostgreSQL compatibility
        safe_name = name.replace("-", "_")
        if not re.match(r"^[a-z][a-z0-9_]{0,62}$", safe_name):
            raise AuthServiceError(
                code="INVALID_IDENTIFIER",
                message=f"Invalid identifier: {name}",
                suggestion="Use lowercase letters, numbers, hyphens, and underscores only",
            )

    def _database_exists(self, project: str) -> bool:
        """Check if auth database exists for a project."""
        db_name = self._auth_db_name(project)

        conn = self._get_admin_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [db_name])
                return cur.fetchone() is not None
        finally:
            conn.close()

    def _create_auth_database(self, project: str) -> AuthDatabaseCredentials:
        """Create a PostgreSQL database for the auth service.

        Creates:
        - Database: {project}_auth_db
        - Role: {project}_auth_user with random password
        """
        db_name = self._auth_db_name(project)
        role_name = self._auth_role_name(project)

        # Validate identifiers
        self._validate_identifier(project)

        # Check if database already exists
        if self._database_exists(project):
            raise AuthServiceError(
                code="AUTH_DATABASE_EXISTS",
                message=f"Auth database '{db_name}' already exists",
                suggestion=(
                    "Disable auth first with 'hostkit auth disable' or use a different project"
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

        return AuthDatabaseCredentials(
            database=db_name,
            username=role_name,
            password=password,
            host=self.config.postgres_host,
            port=self.config.postgres_port,
        )

    def _delete_auth_database(self, project: str) -> None:
        """Delete the auth database and role for a project."""
        db_name = self._auth_db_name(project)
        role_name = self._auth_role_name(project)

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

    def _apply_schema(self, project: str, credentials: AuthDatabaseCredentials) -> None:
        """Apply the auth database schema to a project's auth database."""
        try:
            # Connect to the auth database as the auth user
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
                    # Enable uuid-ossp extension for gen_random_uuid()
                    # Note: This requires superuser, so we use the admin connection
                    pass  # Extension will be enabled separately

                # Apply schema
                with conn.cursor() as cur:
                    cur.execute(AUTH_SCHEMA_SQL)

            finally:
                conn.close()

        except psycopg2.Error as e:
            raise AuthServiceError(
                code="SCHEMA_APPLY_FAILED",
                message=f"Failed to apply auth schema: {e}",
                suggestion="Check database connection and permissions",
            )

        # Enable uuid extension using admin connection
        admin_conn = self._get_admin_connection()
        try:
            with admin_conn.cursor() as cur:
                # Connect to the auth database and enable extension
                cur.execute(
                    sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                        sql.Identifier(credentials.database),
                        sql.Identifier(credentials.username),
                    )
                )
        finally:
            admin_conn.close()

        # Now connect to auth db as admin to enable extension
        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database=credentials.database,
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            finally:
                conn.close()

        except psycopg2.Error as e:
            raise AuthServiceError(
                code="EXTENSION_FAILED",
                message=f"Failed to enable pgcrypto extension: {e}",
                suggestion="Ensure PostgreSQL pgcrypto extension is available",
            )

    def _generate_rsa_keypair(self, project: str) -> tuple[Path, Path]:
        """Generate RSA keypair for JWT signing.

        Creates:
        - /home/{project}/.auth/jwt_private.pem
        - /home/{project}/.auth/jwt_public.pem

        Returns:
            Tuple of (private_key_path, public_key_path)
        """
        auth_dir = self._auth_dir(project)
        auth_dir.mkdir(parents=True, exist_ok=True)

        private_key_path = auth_dir / "jwt_private.pem"
        public_key_path = auth_dir / "jwt_public.pem"

        # Generate RSA key pair (2048 bits for RS256)
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        # Serialize private key
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # Serialize public key
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        # Write keys to files
        private_key_path.write_bytes(private_pem)
        public_key_path.write_bytes(public_pem)

        # Set restrictive permissions on private key
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)

        # Set ownership to project user
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(private_key_path)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(public_key_path)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(auth_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # May fail if not running as root, which is fine for local dev
            pass

        return private_key_path, public_key_path

    def _remove_rsa_keypair(self, project: str) -> None:
        """Remove the RSA keypair for a project."""
        auth_dir = self._auth_dir(project)
        private_key_path = auth_dir / "jwt_private.pem"
        public_key_path = auth_dir / "jwt_public.pem"

        if private_key_path.exists():
            private_key_path.unlink()
        if public_key_path.exists():
            public_key_path.unlink()

    def _update_project_env(
        self,
        project: str,
        credentials: AuthDatabaseCredentials,
        auth_port: int,
        public_key_path: Path | None = None,
    ) -> None:
        """Update a project's .env file with auth service credentials.

        Args:
            project: Project name
            credentials: Database credentials
            auth_port: Port for auth service
            public_key_path: Path to JWT public key (content will be inlined)
        """
        env_path = Path(f"/home/{project}/.env")

        if not env_path.exists():
            raise AuthServiceError(
                code="ENV_NOT_FOUND",
                message=f"Environment file not found: {env_path}",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Read public key content if path provided
        jwt_public_key_content = ""
        if public_key_path and public_key_path.exists():
            # Read the PEM content and escape newlines for .env format
            pem_content = public_key_path.read_text().strip()
            # Replace actual newlines with \n escape sequence for .env
            jwt_public_key_content = pem_content.replace("\n", "\\n")

        # AUTH_URL is always the internal localhost URL for server-side calls
        auth_url = f"http://127.0.0.1:{auth_port}"

        # NEXT_PUBLIC_AUTH_URL is the external domain URL for client-side calls
        db = get_db()
        domains = db.list_domains(project)
        next_public_auth_url = ""
        if domains:
            primary_domain = domains[0]["domain"]
            # Use HTTPS if SSL is enabled, otherwise HTTP
            protocol = "https" if domains[0].get("ssl_enabled") else "http"
            next_public_auth_url = f"{protocol}://{primary_domain}"

        # Read existing content
        with open(env_path) as f:
            lines = f.readlines()

        # Check if AUTH_DB_URL already exists
        has_auth_db = any(
            line.startswith("AUTH_DB_URL=") or line.startswith("# AUTH_DB_URL=") for line in lines
        )

        if has_auth_db:
            # Update existing
            new_lines = []
            for line in lines:
                if line.startswith("AUTH_DB_URL=") or line.startswith("# AUTH_DB_URL="):
                    new_lines.append(f"AUTH_DB_URL={credentials.connection_url}\n")
                elif line.startswith("AUTH_SERVICE_PORT="):
                    new_lines.append(f"AUTH_SERVICE_PORT={auth_port}\n")
                elif line.startswith("AUTH_ENABLED="):
                    new_lines.append("AUTH_ENABLED=true\n")
                elif line.startswith("AUTH_URL="):
                    # AUTH_URL is always the internal localhost URL
                    new_lines.append(f"AUTH_URL={auth_url}\n")
                elif line.startswith("NEXT_PUBLIC_AUTH_URL="):
                    # Update NEXT_PUBLIC_AUTH_URL with current domain
                    if next_public_auth_url:
                        new_lines.append(f"NEXT_PUBLIC_AUTH_URL={next_public_auth_url}\n")
                    else:
                        new_lines.append(line)
                elif line.startswith("AUTH_JWT_PUBLIC_KEY="):
                    # Update with actual PEM content
                    if jwt_public_key_content:
                        new_lines.append(f'AUTH_JWT_PUBLIC_KEY="{jwt_public_key_content}"\n')
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            # Add AUTH_URL if not present (always set to internal localhost URL)
            if not any(line.startswith("AUTH_URL=") for line in lines):
                # Find where to insert (after AUTH_ENABLED or at end of auth block)
                for i, line in enumerate(new_lines):
                    if line.startswith("AUTH_ENABLED="):
                        new_lines.insert(i + 1, f"AUTH_URL={auth_url}\n")
                        break
            # Add NEXT_PUBLIC_AUTH_URL if not present and we have a domain
            if next_public_auth_url and not any(
                line.startswith("NEXT_PUBLIC_AUTH_URL=") for line in lines
            ):
                for i, line in enumerate(new_lines):
                    if line.startswith("AUTH_URL="):
                        new_lines.insert(i + 1, f"NEXT_PUBLIC_AUTH_URL={next_public_auth_url}\n")
                        break
            # Add AUTH_JWT_PUBLIC_KEY if not present and we have key content
            if jwt_public_key_content and not any(
                line.startswith("AUTH_JWT_PUBLIC_KEY=") for line in lines
            ):
                for i, line in enumerate(new_lines):
                    if line.startswith("AUTH_DB_URL="):
                        new_lines.insert(i + 1, f'AUTH_JWT_PUBLIC_KEY="{jwt_public_key_content}"\n')
                        break
        else:
            # Add new auth block with inline public key, AUTH_URL, and NEXT_PUBLIC_AUTH_URL
            next_public_line = (
                f"NEXT_PUBLIC_AUTH_URL={next_public_auth_url}\n" if next_public_auth_url else ""
            )
            auth_block = f"""
# Authentication Service (HostKit managed)
AUTH_ENABLED=true
AUTH_URL={auth_url}
{next_public_line}AUTH_SERVICE_PORT={auth_port}
AUTH_DB_URL={credentials.connection_url}
AUTH_JWT_PUBLIC_KEY="{jwt_public_key_content}"
"""
            new_lines = lines + [auth_block]

        # Write back
        with open(env_path, "w") as f:
            f.writelines(new_lines)

        # Ensure correct ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_path)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

    def _remove_auth_from_env(self, project: str) -> None:
        """Remove auth service configuration from a project's .env file."""
        env_path = Path(f"/home/{project}/.env")

        if not env_path.exists():
            return  # Nothing to do

        # Read existing content
        with open(env_path) as f:
            lines = f.readlines()

        # Remove auth-related lines
        new_lines = []
        skip_block = False

        for line in lines:
            # Skip the auth block header and following lines
            if line.strip() == "# Authentication Service (HostKit managed)":
                skip_block = True
                continue
            if skip_block:
                if line.startswith("AUTH_"):
                    continue
                elif line.strip() == "":
                    skip_block = False
                    continue
                else:
                    skip_block = False

            # Also remove standalone AUTH_ lines
            if line.startswith("AUTH_"):
                continue

            new_lines.append(line)

        # Write back
        with open(env_path, "w") as f:
            f.writelines(new_lines)

    def auth_is_enabled(self, project: str) -> bool:
        """Check if authentication is enabled for a project."""
        return self._database_exists(project)

    def get_auth_config(self, project: str) -> AuthConfig | None:
        """Get authentication configuration for a project.

        Returns None if auth is not enabled.
        """
        if not self.auth_is_enabled(project):
            return None

        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            return None

        auth_dir = self._auth_dir(project)
        auth_port = project_data["port"] + 1000

        return AuthConfig(
            project=project,
            enabled=True,
            port=auth_port,
            auth_db=self._auth_db_name(project),
            auth_db_user=self._auth_role_name(project),
            jwt_public_key_path=str(auth_dir / "jwt_public.pem"),
            jwt_private_key_path=str(auth_dir / "jwt_private.pem"),
            # OAuth config would be loaded from .env or database
        )

    def list_auth_projects(self) -> list[AuthConfig]:
        """List all projects with authentication enabled."""
        projects = self.hostkit_db.list_projects()
        auth_configs = []

        for project in projects:
            config = self.get_auth_config(project["name"])
            if config:
                auth_configs.append(config)

        return auth_configs

    def _auth_service_name(self, project: str) -> str:
        """Generate systemd service name for auth service."""
        return f"hostkit-{project}-auth"

    def _get_templates_dir(self) -> Path:
        """Get the path to the auth templates directory."""
        # In development, templates are relative to the package
        # In production, they're installed to /var/lib/hostkit/templates/
        dev_path = Path(__file__).parent.parent.parent.parent / "templates" / "auth"
        prod_path = Path("/var/lib/hostkit/templates/auth")

        if prod_path.exists():
            return prod_path
        elif dev_path.exists():
            return dev_path
        else:
            raise AuthServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Auth service templates not found",
                suggestion="Ensure HostKit is properly installed",
            )

    def _deploy_auth_service(self, project: str, credentials: AuthDatabaseCredentials) -> None:
        """Deploy the FastAPI auth service for a project.

        Steps:
        1. Copy auth service template files to /home/{project}/.auth/
        2. Create Python virtual environment
        3. Install dependencies
        4. Generate systemd service file
        5. Create log files
        """
        import shutil

        auth_dir = self._auth_dir(project)
        templates_dir = self._get_templates_dir()

        # Step 1: Copy template files
        if auth_dir.exists():
            # Clear existing files (except jwt keys)
            for item in auth_dir.iterdir():
                if item.name not in ("jwt_private.pem", "jwt_public.pem"):
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
        else:
            auth_dir.mkdir(parents=True, exist_ok=True)

        # Get template context for rendering config.py
        auth_port = self._auth_port(project)
        # Always use hostkit.dev subdomain for auth (canonical URL for OAuth callbacks)
        primary_domain = f"{project}.hostkit.dev"
        base_url = f"https://{primary_domain}"

        template_context = {
            "project": project,
            "auth_port": auth_port,
            "domain": primary_domain,
            "base_url": base_url,
        }

        # Copy all template files (render config.py as Jinja2 template)
        for src_file in templates_dir.rglob("*"):
            if src_file.is_file():
                rel_path = src_file.relative_to(templates_dir)
                dest_file = auth_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)

                if src_file.name == "config.py":
                    # Render config.py as a Jinja2 template
                    template = Template(src_file.read_text())
                    dest_file.write_text(template.render(**template_context))
                else:
                    shutil.copy2(src_file, dest_file)

        # Step 1.5: Create .auth/.env file with required environment variables
        env_file = auth_dir / ".env"
        env_content = f"""# HostKit Auth Service Environment
# Generated by HostKit - DO NOT EDIT MANUALLY

# Database
AUTH_DB_URL={credentials.connection_url}

# Server
AUTH_SERVICE_PORT={auth_port}
PROJECT_NAME={project}
BASE_URL={base_url}

# JWT Keys
JWT_PRIVATE_KEY_PATH={auth_dir}/jwt_private.pem
JWT_PUBLIC_KEY_PATH={auth_dir}/jwt_public.pem

# Features
EMAIL_ENABLED=true
MAGIC_LINK_ENABLED=true
ANONYMOUS_ENABLED=true

# Logging
LOG_LEVEL=INFO
"""
        env_file.write_text(env_content)
        env_file.chmod(0o600)

        # Step 2: Create virtual environment
        venv_path = auth_dir / "venv"
        try:
            subprocess.run(
                ["python3", "-m", "venv", str(venv_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise AuthServiceError(
                code="VENV_CREATE_FAILED",
                message=f"Failed to create virtual environment: {stderr}",
                suggestion="Ensure python3-venv is installed",
            )

        # Step 3: Install dependencies
        pip_path = venv_path / "bin" / "pip"
        requirements_path = auth_dir / "requirements.txt"
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_path)],
                check=True,
                capture_output=True,
                timeout=300,  # 5 minutes for pip install
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise AuthServiceError(
                code="PIP_INSTALL_FAILED",
                message=f"Failed to install dependencies: {stderr}",
                suggestion="Check requirements.txt and network connectivity",
            )

        # Step 4: Generate systemd service file
        service_template_path = (
            Path(__file__).parent.parent.parent.parent / "templates" / "auth.service.j2"
        )
        if not service_template_path.exists():
            service_template_path = Path("/var/lib/hostkit/templates/auth.service.j2")

        if not service_template_path.exists():
            raise AuthServiceError(
                code="SERVICE_TEMPLATE_NOT_FOUND",
                message="Auth service systemd template not found",
                suggestion="Ensure HostKit is properly installed",
            )

        auth_port = self._auth_port(project)
        template = Template(service_template_path.read_text())
        service_content = template.render(
            project_name=project,
            auth_port=auth_port,
            auth_db_url=credentials.connection_url,
        )

        service_name = self._auth_service_name(project)
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        service_path.write_text(service_content)

        # Step 5: Create log files
        log_dir = Path(f"/var/log/projects/{project}")
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "auth.log").touch()
        (log_dir / "auth-error.log").touch()

        # Set ownership
        try:
            subprocess.run(
                ["chown", "-R", f"{project}:{project}", str(auth_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "auth.log")],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["chown", f"{project}:{project}", str(log_dir / "auth-error.log")],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

    def _start_auth_service(self, project: str) -> None:
        """Start and enable the auth service."""
        service_name = self._auth_service_name(project)

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
            raise AuthServiceError(
                code="SERVICE_START_FAILED",
                message=f"Failed to start auth service: {stderr}",
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _stop_auth_service(self, project: str) -> None:
        """Stop and disable the auth service."""
        service_name = self._auth_service_name(project)
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

    def _restart_auth_service(self, project: str) -> None:
        """Restart the auth service to pick up configuration changes."""
        service_name = self._auth_service_name(project)
        service_path = Path(f"/etc/systemd/system/{service_name}.service")

        if not service_path.exists():
            return  # Service doesn't exist

        try:
            subprocess.run(
                ["systemctl", "restart", f"{service_name}.service"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else "unknown error"
            raise AuthServiceError(
                code="SERVICE_RESTART_FAILED",
                message=f"Failed to restart auth service: {stderr}",
                suggestion=f"Check logs: journalctl -u {service_name}.service",
            )

    def _sync_oauth_to_auth_env(
        self,
        project: str,
        google_client_id: str | None = None,
        google_web_client_id: str | None = None,
        google_client_secret: str | None = None,
        apple_client_id: str | None = None,
        apple_team_id: str | None = None,
        apple_key_id: str | None = None,
        email_enabled: bool | None = None,
        magic_link_enabled: bool | None = None,
        anonymous_enabled: bool | None = None,
    ) -> None:
        """Sync OAuth and feature flag configuration to the auth service's .env file.

        Updates existing values or adds new ones. Does not remove values
        (pass empty string to clear a value).

        Args:
            project: Project name
            google_client_id: Google OAuth client ID (for native apps)
            google_web_client_id: Google OAuth web client ID (for web OAuth)
            google_client_secret: Google OAuth client secret
            apple_client_id: Apple Sign-In client ID
            apple_team_id: Apple Developer Team ID
            apple_key_id: Apple Sign-In key ID
            email_enabled: Enable/disable email/password auth
            magic_link_enabled: Enable/disable magic link auth
            anonymous_enabled: Enable/disable anonymous sessions
        """
        import re

        auth_dir = self._auth_dir(project)
        env_file = auth_dir / ".env"

        if not env_file.exists():
            return

        env_content = env_file.read_text()
        lines = env_content.split("\n")

        # Map of env var names to new values (only include non-None values)
        updates: dict[str, str] = {}
        if google_client_id is not None:
            updates["GOOGLE_CLIENT_ID"] = google_client_id
        if google_web_client_id is not None:
            updates["GOOGLE_WEB_CLIENT_ID"] = google_web_client_id
        if google_client_secret is not None:
            updates["GOOGLE_CLIENT_SECRET"] = google_client_secret
        if apple_client_id is not None:
            updates["APPLE_CLIENT_ID"] = apple_client_id
        if apple_team_id is not None:
            updates["APPLE_TEAM_ID"] = apple_team_id
        if apple_key_id is not None:
            updates["APPLE_KEY_ID"] = apple_key_id
        if email_enabled is not None:
            updates["EMAIL_ENABLED"] = str(email_enabled).lower()
        if magic_link_enabled is not None:
            updates["MAGIC_LINK_ENABLED"] = str(magic_link_enabled).lower()
        if anonymous_enabled is not None:
            updates["ANONYMOUS_ENABLED"] = str(anonymous_enabled).lower()

        if not updates:
            return  # Nothing to update

        # Track which vars we've updated
        updated_vars: set[str] = set()

        # Update existing lines
        new_lines = []
        for line in lines:
            updated = False
            for var_name, var_value in updates.items():
                # Match lines like VAR_NAME=value or VAR_NAME="value"
                pattern = f"^{re.escape(var_name)}="
                if re.match(pattern, line):
                    new_lines.append(f"{var_name}={var_value}")
                    updated_vars.add(var_name)
                    updated = True
                    break
            if not updated:
                new_lines.append(line)

        # Add any vars that weren't already in the file
        missing_vars = set(updates.keys()) - updated_vars
        if missing_vars:
            # Check if we already have an OAuth section
            has_oauth_section = any("# OAuth" in line for line in new_lines)
            if not has_oauth_section and any(
                v.startswith("GOOGLE_") or v.startswith("APPLE_") for v in missing_vars
            ):
                new_lines.append("")
                new_lines.append("# OAuth Configuration")

            for var_name in sorted(missing_vars):
                new_lines.append(f"{var_name}={updates[var_name]}")

        # Write back
        env_file.write_text("\n".join(new_lines))

        # Ensure correct ownership
        try:
            subprocess.run(
                ["chown", f"{project}:{project}", str(env_file)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # May fail if not root

    def _remove_auth_service(self, project: str) -> None:
        """Remove the auth service completely.

        Stops service, removes systemd unit file, removes auth directory.
        """
        import shutil

        # Stop the service first
        self._stop_auth_service(project)

        # Remove systemd service file
        service_name = self._auth_service_name(project)
        service_path = Path(f"/etc/systemd/system/{service_name}.service")
        if service_path.exists():
            service_path.unlink()
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # Remove auth directory (but keep parent project directory)
        auth_dir = self._auth_dir(project)
        if auth_dir.exists():
            shutil.rmtree(auth_dir)

    def _configure_nginx_auth(self, project: str) -> None:
        """Configure Nginx to route /auth/* to the auth service.

        This adds a location block to the project's Nginx config.
        """
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.add_auth_location(project, self._auth_port(project))

    def _remove_nginx_auth(self, project: str) -> None:
        """Remove the /auth/* location block from Nginx config."""
        from hostkit.services.nginx_service import NginxService

        nginx = NginxService()
        nginx.remove_auth_location(project)

    def _auto_configure_oauth_from_platform(self, project: str) -> None:
        """Auto-configure OAuth from platform credentials if available.

        Reads from /etc/hostkit/oauth.ini and updates the auth service .env
        and HostKit database with OAuth credentials.

        This is a best-effort operation - failures are logged but don't fail
        the auth enable process.
        """
        import configparser
        import logging

        logger = logging.getLogger(__name__)
        platform_config_path = Path("/etc/hostkit/oauth.ini")

        if not platform_config_path.exists():
            logger.debug("No platform OAuth config found, skipping auto-configuration")
            return

        try:
            config = configparser.ConfigParser()
            config.read(platform_config_path)

            # Read Google OAuth credentials
            google_client_id = None
            google_web_client_id = None
            google_client_secret = None
            if config.has_section("google"):
                google_client_id = config.get("google", "client_id", fallback=None)
                google_web_client_id = config.get("google", "web_client_id", fallback=None)
                google_client_secret = config.get("google", "client_secret", fallback=None)

            # Read Apple Sign-In credentials
            apple_client_id = None
            apple_team_id = None
            apple_key_id = None
            if config.has_section("apple"):
                apple_client_id = config.get("apple", "client_id", fallback=None)
                apple_team_id = config.get("apple", "team_id", fallback=None)
                apple_key_id = config.get("apple", "key_id", fallback=None)

            # Update database record with OAuth credentials
            if any([google_client_id, google_web_client_id, apple_client_id]):
                self.hostkit_db.update_auth_service(
                    project=project,
                    google_client_id=google_client_id,
                    google_web_client_id=google_web_client_id,
                    google_client_secret=google_client_secret,
                    apple_client_id=apple_client_id,
                    apple_team_id=apple_team_id,
                    apple_key_id=apple_key_id,
                )

                # Sync OAuth credentials to auth service .env file
                self._sync_oauth_to_auth_env(
                    project=project,
                    google_client_id=google_client_id,
                    google_web_client_id=google_web_client_id,
                    google_client_secret=google_client_secret,
                    apple_client_id=apple_client_id,
                    apple_team_id=apple_team_id,
                    apple_key_id=apple_key_id,
                )

                logger.info(f"Auto-configured OAuth from platform for project '{project}'")

        except Exception as e:
            # Don't fail auth enable if OAuth auto-config fails
            logger.warning(f"Failed to auto-configure OAuth from platform: {e}")

    def enable_auth(
        self,
        project: str,
        google_client_id: str | None = None,
        google_web_client_id: str | None = None,
        google_client_secret: str | None = None,
        apple_client_id: str | None = None,
        apple_team_id: str | None = None,
        apple_key_id: str | None = None,
        email_enabled: bool = True,
        magic_link_enabled: bool = True,
        anonymous_enabled: bool = True,
    ) -> AuthConfig:
        """Enable authentication service for a project.

        Creates:
        - PostgreSQL database: {project}_auth_db
        - Database role: {project}_auth_user
        - RSA keypair for JWT signing
        - Updates project .env with auth configuration
        - Registers auth service in HostKit database

        Args:
            project: Project name
            google_client_id: Google OAuth client ID (for native iOS/Android apps)
            google_web_client_id: Google OAuth web client ID (for web OAuth)
            google_client_secret: Google OAuth client secret
            apple_client_id: Apple Sign-In client ID
            apple_team_id: Apple Developer Team ID
            apple_key_id: Apple Sign-In key ID
            email_enabled: Enable email/password authentication
            magic_link_enabled: Enable magic link authentication
            anonymous_enabled: Enable anonymous sessions

        Returns:
            AuthConfig with the enabled configuration
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Check if already enabled
        if self.auth_is_enabled(project):
            raise AuthServiceError(
                code="AUTH_ALREADY_ENABLED",
                message=f"Authentication is already enabled for '{project}'",
                suggestion="Use 'hostkit auth disable' first to reset configuration",
            )

        # Calculate auth port
        auth_port = self._auth_port(project)

        # Step 1: Create auth database
        credentials = self._create_auth_database(project)

        try:
            # Step 2: Apply schema to auth database
            self._apply_schema(project, credentials)

            # Step 3: Generate RSA keypair for JWT signing
            private_key_path, public_key_path = self._generate_rsa_keypair(project)

            # Step 4: Update project .env file (with inline public key for Edge runtime)
            self._update_project_env(project, credentials, auth_port, public_key_path)

            # Step 5: Deploy the FastAPI auth service
            self._deploy_auth_service(project, credentials)

            # Step 6: Configure Nginx to route /auth/* to auth service
            self._configure_nginx_auth(project)

            # Step 7: Register in HostKit database
            self.hostkit_db.create_auth_service(
                project=project,
                auth_port=auth_port,
                auth_db_name=credentials.database,
                auth_db_user=credentials.username,
                google_client_id=google_client_id,
                google_web_client_id=google_web_client_id,
                google_client_secret=google_client_secret,
                apple_client_id=apple_client_id,
                apple_team_id=apple_team_id,
                apple_key_id=apple_key_id,
                email_enabled=email_enabled,
                magic_link_enabled=magic_link_enabled,
                anonymous_enabled=anonymous_enabled,
            )

            # Step 8: Start the auth service
            self._start_auth_service(project)

            # Step 9: Regenerate nginx port mappings for wildcard routing
            from hostkit.services.project_service import ProjectService

            ProjectService()._regenerate_nginx_port_mappings()

            # Step 10: Auto-configure OAuth from platform if available
            self._auto_configure_oauth_from_platform(project)

        except Exception as e:
            # Rollback: clean up all created resources
            try:
                self._remove_auth_service(project)
            except Exception:
                pass
            try:
                self._remove_nginx_auth(project)
            except Exception:
                pass
            try:
                self._delete_auth_database(project)
            except Exception:
                pass
            try:
                self._remove_rsa_keypair(project)
            except Exception:
                pass
            try:
                self._remove_auth_from_env(project)
            except Exception:
                pass
            raise AuthServiceError(
                code="AUTH_ENABLE_FAILED",
                message=f"Failed to enable auth: {e}",
                suggestion="Check PostgreSQL is running and has sufficient permissions",
            )

        return AuthConfig(
            project=project,
            enabled=True,
            port=auth_port,
            auth_db=credentials.database,
            auth_db_user=credentials.username,
            jwt_public_key_path=str(public_key_path),
            jwt_private_key_path=str(private_key_path),
            google_client_id=google_client_id,
            google_web_client_id=google_web_client_id,
            google_client_secret=google_client_secret,
            apple_client_id=apple_client_id,
            apple_team_id=apple_team_id,
            apple_key_id=apple_key_id,
            email_enabled=email_enabled,
            magic_link_enabled=magic_link_enabled,
            anonymous_enabled=anonymous_enabled,
        )

    def disable_auth(self, project: str, force: bool = False) -> None:
        """Disable authentication service for a project.

        Removes:
        - PostgreSQL database and role
        - RSA keypair
        - Auth configuration from project .env
        - HostKit database record

        Args:
            project: Project name
            force: Must be True to confirm deletion
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if auth is enabled
        if not self.auth_is_enabled(project):
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion="Nothing to disable",
            )

        # Require force flag
        if not force:
            raise AuthServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable authentication",
                suggestion="Add --force to confirm: 'hostkit auth disable {project} --force'",
            )

        # Step 1: Stop and remove auth service
        self._remove_auth_service(project)

        # Step 2: Remove Nginx auth configuration
        self._remove_nginx_auth(project)

        # Step 3: Delete auth database and role
        self._delete_auth_database(project)

        # Step 4: Remove RSA keypair
        self._remove_rsa_keypair(project)

        # Step 5: Remove auth config from .env
        self._remove_auth_from_env(project)

        # Step 6: Remove from HostKit database
        self.hostkit_db.delete_auth_service(project)

    def update_auth_config(
        self,
        project: str,
        google_client_id: str | None = None,
        google_web_client_id: str | None = None,
        google_client_secret: str | None = None,
        apple_client_id: str | None = None,
        apple_team_id: str | None = None,
        apple_key_id: str | None = None,
        email_enabled: bool | None = None,
        magic_link_enabled: bool | None = None,
        anonymous_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update authentication configuration for a project.

        Args:
            project: Project name
            google_client_id: Google OAuth client ID (for native iOS/Android apps)
            google_web_client_id: Google OAuth web client ID (for web OAuth)
            google_client_secret: Google OAuth client secret
            apple_client_id: Apple Sign-In client ID
            apple_team_id: Apple Developer Team ID
            apple_key_id: Apple Sign-In key ID
            email_enabled: Enable/disable email/password auth
            magic_link_enabled: Enable/disable magic link auth
            anonymous_enabled: Enable/disable anonymous sessions

        Returns:
            Updated configuration dictionary
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Check if auth is enabled
        auth_record = self.hostkit_db.get_auth_service(project)
        if not auth_record:
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        # Update the configuration in the database
        updated = self.hostkit_db.update_auth_service(
            project=project,
            google_client_id=google_client_id,
            google_web_client_id=google_web_client_id,
            google_client_secret=google_client_secret,
            apple_client_id=apple_client_id,
            apple_team_id=apple_team_id,
            apple_key_id=apple_key_id,
            email_enabled=email_enabled,
            magic_link_enabled=magic_link_enabled,
            anonymous_enabled=anonymous_enabled,
        )

        # Sync OAuth and feature flags to auth service's .env file
        self._sync_oauth_to_auth_env(
            project=project,
            google_client_id=google_client_id,
            google_web_client_id=google_web_client_id,
            google_client_secret=google_client_secret,
            apple_client_id=apple_client_id,
            apple_team_id=apple_team_id,
            apple_key_id=apple_key_id,
            email_enabled=email_enabled,
            magic_link_enabled=magic_link_enabled,
            anonymous_enabled=anonymous_enabled,
        )

        # Restart auth service to pick up the new configuration
        self._restart_auth_service(project)

        return {
            "project": project,
            "auth_port": updated["auth_port"],
            "email_enabled": bool(updated["email_enabled"]),
            "magic_link_enabled": bool(updated["magic_link_enabled"]),
            "anonymous_enabled": bool(updated["anonymous_enabled"]),
            "google_configured": bool(updated["google_client_id"]),
            "apple_configured": bool(updated["apple_client_id"]),
            "updated_at": updated["updated_at"],
            "service_restarted": True,
        }

    def get_auth_config_details(self, project: str) -> dict[str, Any]:
        """Get detailed authentication configuration for a project.

        Args:
            project: Project name

        Returns:
            Configuration dictionary with all settings
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        auth_record = self.hostkit_db.get_auth_service(project)
        if not auth_record:
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        return {
            "project": project,
            "auth_port": auth_record["auth_port"],
            "auth_db": auth_record["auth_db_name"],
            "auth_db_user": auth_record["auth_db_user"],
            "email_enabled": bool(auth_record["email_enabled"]),
            "magic_link_enabled": bool(auth_record["magic_link_enabled"]),
            "anonymous_enabled": bool(auth_record["anonymous_enabled"]),
            "google_client_id": auth_record["google_client_id"],
            "google_client_secret": auth_record["google_client_secret"],
            "apple_client_id": auth_record["apple_client_id"],
            "apple_team_id": auth_record["apple_team_id"],
            "apple_key_id": auth_record["apple_key_id"],
            "created_at": auth_record["created_at"],
            "updated_at": auth_record["updated_at"],
        }

    def list_auth_users(
        self,
        project: str,
        limit: int = 50,
        verified_only: bool = False,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """List users from a project's auth database.

        Args:
            project: Project name
            limit: Maximum number of users to return
            verified_only: Only return email-verified users
            provider: Filter by auth provider (email, google, apple, anonymous)

        Returns:
            List of user dictionaries
        """
        # Validate project exists
        project_data = self.hostkit_db.get_project(project)
        if not project_data:
            raise AuthServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        # Get auth service record to find database credentials
        auth_record = self.hostkit_db.get_auth_service(project)
        if not auth_record:
            raise AuthServiceError(
                code="AUTH_NOT_ENABLED",
                message=f"Authentication is not enabled for '{project}'",
                suggestion=f"Enable auth first with 'hostkit auth enable {project}'",
            )

        db_name = auth_record["auth_db_name"]
        db_user = auth_record["auth_db_user"]

        # We need to get the password from the project's .env file
        env_path = Path(f"/home/{project}/.env")
        db_password = None

        if env_path.exists():
            import re

            with open(env_path) as f:
                content = f.read()
                # Extract password from AUTH_DB_URL
                match = re.search(r"AUTH_DB_URL=postgresql://[^:]+:([^@]+)@", content)
                if match:
                    from urllib.parse import unquote

                    db_password = unquote(match.group(1))

        if not db_password:
            raise AuthServiceError(
                code="AUTH_DB_CREDS_NOT_FOUND",
                message="Cannot find auth database credentials",
                suggestion="Check that AUTH_DB_URL is set in the project's .env file",
            )

        # Connect to the auth database
        try:
            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=db_user,
                password=db_password,
                database=db_name,
            )

            try:
                with conn.cursor() as cur:
                    # Build the query based on filters
                    where_clauses = []
                    params: list[Any] = []

                    if verified_only:
                        where_clauses.append("u.email_verified = TRUE")

                    if provider:
                        if provider == "anonymous":
                            where_clauses.append("u.is_anonymous = TRUE")
                        elif provider == "email":
                            where_clauses.append(
                                "u.is_anonymous = FALSE AND NOT EXISTS ("
                                "SELECT 1 FROM oauth_accounts o WHERE o.user_id = u.id)"
                            )
                        elif provider in ("google", "apple"):
                            where_clauses.append(
                                "EXISTS (SELECT 1 FROM oauth_accounts o "
                                "WHERE o.user_id = u.id AND o.provider = %s)"
                            )
                            params.append(provider)

                    where_sql = ""
                    if where_clauses:
                        where_sql = "WHERE " + " AND ".join(where_clauses)

                    query = f"""
                        SELECT
                            u.id::text,
                            u.email,
                            u.email_verified,
                            u.is_anonymous,
                            u.created_at,
                            u.last_sign_in_at,
                            COALESCE(
                                (SELECT array_agg(DISTINCT o.provider)
                                 FROM oauth_accounts o
                                 WHERE o.user_id = u.id),
                                ARRAY[]::text[]
                            ) as providers
                        FROM users u
                        {where_sql}
                        ORDER BY u.created_at DESC
                        LIMIT %s
                    """
                    params.append(limit)

                    cur.execute(query, params)
                    rows = cur.fetchall()

                    users = []
                    for row in rows:
                        user = {
                            "id": row[0],
                            "email": row[1],
                            "email_verified": row[2],
                            "is_anonymous": row[3],
                            "created_at": row[4].isoformat() if row[4] else None,
                            "last_sign_in_at": row[5].isoformat() if row[5] else None,
                            "providers": list(row[6]) if row[6] else [],
                        }
                        users.append(user)

                    return users

            finally:
                conn.close()

        except psycopg2.Error as e:
            raise AuthServiceError(
                code="AUTH_DB_QUERY_FAILED",
                message=f"Failed to query auth database: {e}",
                suggestion="Check that the auth database is accessible",
            )

    def get_auth_status(self, project: str | None = None) -> dict[str, Any]:
        """Get authentication status for a project or all projects.

        Args:
            project: Optional project name. If None, returns status for all projects.

        Returns:
            Dictionary with auth status information
        """
        if project:
            # Single project status
            project_data = self.hostkit_db.get_project(project)
            if not project_data:
                raise AuthServiceError(
                    code="PROJECT_NOT_FOUND",
                    message=f"Project '{project}' does not exist",
                    suggestion="Check the project name with 'hostkit project list'",
                )

            auth_record = self.hostkit_db.get_auth_service(project)
            auth_dir = self._auth_dir(project)

            if not auth_record:
                return {
                    "project": project,
                    "enabled": False,
                    "auth_port": None,
                    "auth_db": None,
                    "providers": {},
                }

            return {
                "project": project,
                "enabled": bool(auth_record["enabled"]),
                "auth_port": auth_record["auth_port"],
                "auth_db": auth_record["auth_db_name"],
                "auth_db_user": auth_record["auth_db_user"],
                "jwt_keys_exist": (auth_dir / "jwt_private.pem").exists(),
                "providers": {
                    "email": bool(auth_record["email_enabled"]),
                    "magic_link": bool(auth_record["magic_link_enabled"]),
                    "anonymous": bool(auth_record["anonymous_enabled"]),
                    "google": bool(auth_record["google_client_id"]),
                    "apple": bool(auth_record["apple_client_id"]),
                },
                "created_at": auth_record["created_at"],
                "updated_at": auth_record["updated_at"],
            }

        else:
            # All projects status
            auth_services = self.hostkit_db.list_auth_services()
            projects = self.hostkit_db.list_projects()

            # Build a map of projects with auth enabled
            auth_map = {s["project"]: s for s in auth_services}

            results = []
            for proj in projects:
                name = proj["name"]
                if name in auth_map:
                    record = auth_map[name]
                    results.append(
                        {
                            "project": name,
                            "enabled": bool(record["enabled"]),
                            "auth_port": record["auth_port"],
                            "providers": {
                                "email": bool(record["email_enabled"]),
                                "google": bool(record["google_client_id"]),
                                "apple": bool(record["apple_client_id"]),
                            },
                        }
                    )
                else:
                    results.append(
                        {
                            "project": name,
                            "enabled": False,
                            "auth_port": None,
                            "providers": {},
                        }
                    )

            return {
                "projects": results,
                "total": len(results),
                "auth_enabled_count": sum(1 for r in results if r["enabled"]),
            }

"""Claude daemon service for HostKit.

Handles setup, enablement, and management of the Claude daemon.
"""

import os
import shutil
import subprocess
import secrets
import hashlib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from hostkit.database import get_db

# Paths
CLAUDE_DAEMON_DIR = Path("/var/lib/hostkit/claude")
CLAUDE_CONFIG_PATH = Path("/etc/hostkit/claude.env")
CLAUDE_TEMPLATES_DIR = Path("/var/lib/hostkit/templates/claude-daemon")
CLAUDE_SERVICE_NAME = "hostkit-claude"
CLAUDE_DB_NAME = "hostkit_claude"
CLAUDE_DB_USER = "hostkit_claude"


class ClaudeServiceError(Exception):
    """Exception raised for Claude service errors."""

    def __init__(
        self,
        message: str,
        code: str = "CLAUDE_ERROR",
        suggestion: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.suggestion = suggestion


@dataclass
class ProjectKeyResult:
    """Result of enabling Claude for a project."""
    project_name: str
    api_key: str
    api_key_prefix: str
    endpoint: str


class ClaudeService:
    """Service for managing the Claude daemon."""

    def __init__(self):
        """Initialize the Claude service."""
        pass

    def _run(
        self,
        cmd: list[str],
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command."""
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=True,
        )

    def _get_db(self):
        """Get database instance."""
        return get_db()

    def setup(
        self,
        api_key: str,
        force: bool = False,
    ) -> dict:
        """Set up the Claude daemon.

        Creates the database, deploys the service code, and starts the service.

        Args:
            api_key: Anthropic API key
            force: Overwrite existing setup

        Returns:
            Dict with setup details

        Raises:
            ClaudeServiceError: If setup fails
        """
        # Check if already set up
        if CLAUDE_DAEMON_DIR.exists() and not force:
            raise ClaudeServiceError(
                message="Claude daemon already set up",
                code="ALREADY_CONFIGURED",
                suggestion="Use --force to overwrite",
            )

        # Check templates exist
        if not CLAUDE_TEMPLATES_DIR.exists():
            raise ClaudeServiceError(
                message="Claude daemon templates not found",
                code="TEMPLATES_NOT_FOUND",
                suggestion="Run deploy.sh to sync templates to VPS",
            )

        # Create database
        db_password = secrets.token_urlsafe(24)
        self._create_database(db_password)

        # Create service directory
        CLAUDE_DAEMON_DIR.mkdir(parents=True, exist_ok=True)

        # Copy template files
        for item in CLAUDE_TEMPLATES_DIR.iterdir():
            if item.is_dir():
                shutil.copytree(
                    item,
                    CLAUDE_DAEMON_DIR / item.name,
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(item, CLAUDE_DAEMON_DIR / item.name)

        # Create virtual environment and install dependencies
        venv_path = CLAUDE_DAEMON_DIR / "venv"
        self._run(["python3", "-m", "venv", str(venv_path)])
        self._run([
            str(venv_path / "bin" / "pip"),
            "install",
            "-r",
            str(CLAUDE_DAEMON_DIR / "requirements.txt"),
        ])

        # Create config file
        database_url = f"postgresql+asyncpg://{CLAUDE_DB_USER}:{db_password}@localhost/{CLAUDE_DB_NAME}"
        self._create_config(api_key, database_url)

        # Install systemd service
        self._install_service()

        # Start service
        self._run(["systemctl", "daemon-reload"])
        self._run(["systemctl", "enable", CLAUDE_SERVICE_NAME])
        self._run(["systemctl", "start", CLAUDE_SERVICE_NAME])

        return {
            "service_url": "http://127.0.0.1:9000",
            "database": CLAUDE_DB_NAME,
            "service_dir": str(CLAUDE_DAEMON_DIR),
            "config_file": str(CLAUDE_CONFIG_PATH),
        }

    def _create_database(self, password: str) -> None:
        """Create the Claude database and user."""
        # Check if user exists
        result = self._run(
            ["sudo", "-u", "postgres", "psql", "-tAc",
             f"SELECT 1 FROM pg_roles WHERE rolname='{CLAUDE_DB_USER}'"],
            check=False,
        )

        if result.returncode != 0 or "1" not in result.stdout:
            # Create user
            self._run([
                "sudo", "-u", "postgres", "psql", "-c",
                f"CREATE USER {CLAUDE_DB_USER} WITH PASSWORD '{password}'",
            ])

        # Check if database exists
        result = self._run(
            ["sudo", "-u", "postgres", "psql", "-tAc",
             f"SELECT 1 FROM pg_database WHERE datname='{CLAUDE_DB_NAME}'"],
            check=False,
        )

        if result.returncode != 0 or "1" not in result.stdout:
            # Create database
            self._run([
                "sudo", "-u", "postgres", "psql", "-c",
                f"CREATE DATABASE {CLAUDE_DB_NAME} OWNER {CLAUDE_DB_USER}",
            ])

    def _create_config(self, api_key: str, database_url: str) -> None:
        """Create the configuration file."""
        secret_key = secrets.token_urlsafe(32)

        config_content = f"""# HostKit Claude Daemon Configuration
# Generated by hostkit claude setup

HOST=127.0.0.1
PORT=9000
DEBUG=false
LOG_LEVEL=info

DATABASE_URL={database_url}

ANTHROPIC_API_KEY={api_key}
ANTHROPIC_MODEL=claude-sonnet-4-20250514
ANTHROPIC_MAX_TOKENS=4096

SECRET_KEY={secret_key}
API_KEY_PREFIX=ck

DEFAULT_RATE_LIMIT_RPM=60
DEFAULT_DAILY_TOKEN_LIMIT=1000000

TOOL_TIMEOUT_SECONDS=30
TOOL_MAX_OUTPUT_SIZE=100000

MAX_MESSAGES_PER_CONVERSATION=1000
MAX_CONVERSATIONS_PER_PROJECT=1000
"""

        CLAUDE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLAUDE_CONFIG_PATH.write_text(config_content)
        os.chmod(CLAUDE_CONFIG_PATH, 0o600)

    def _install_service(self) -> None:
        """Install the systemd service."""
        service_content = f"""[Unit]
Description=HostKit Claude Daemon
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory={CLAUDE_DAEMON_DIR}
Environment=PYTHONPATH={CLAUDE_DAEMON_DIR}
ExecStart={CLAUDE_DAEMON_DIR}/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 9000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

        service_path = Path(f"/etc/systemd/system/{CLAUDE_SERVICE_NAME}.service")
        service_path.write_text(service_content)

    def status(self) -> dict:
        """Get Claude daemon status."""
        # Check if configured
        if not CLAUDE_CONFIG_PATH.exists():
            return {
                "status": "not_configured",
                "message": "Claude daemon not set up. Run 'hostkit claude setup'",
            }

        # Check service status
        result = self._run(
            ["systemctl", "is-active", CLAUDE_SERVICE_NAME],
            check=False,
        )
        service_status = result.stdout.strip() if result.returncode == 0 else "stopped"

        # Get database connection status
        try:
            # Try to connect to database
            result = self._run(
                ["sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-c", "SELECT 1"],
                check=False,
            )
            db_status = "connected" if result.returncode == 0 else "disconnected"
        except Exception:
            db_status = "error"

        # Count enabled projects
        project_count = self._count_enabled_projects()

        return {
            "status": service_status,
            "database": db_status,
            "endpoint": "http://127.0.0.1:9000" if service_status == "active" else None,
            "project_count": project_count,
            "config_file": str(CLAUDE_CONFIG_PATH),
        }

    def _count_enabled_projects(self) -> int:
        """Count projects with Claude enabled.

        Note: For now, counts projects in the daemon database.
        """
        result = self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-tAc",
            "SELECT COUNT(*) FROM project_keys WHERE enabled = true"
        ], check=False)

        if result.returncode == 0 and result.stdout.strip():
            try:
                return int(result.stdout.strip())
            except ValueError:
                return 0
        return 0

    def enable_project(self, project_name: str) -> ProjectKeyResult:
        """Enable Claude for a project.

        Generates an API key and stores it in the daemon database.

        Args:
            project_name: Name of the project

        Returns:
            ProjectKeyResult with API key details

        Raises:
            ClaudeServiceError: If enabling fails
        """
        # Check daemon is set up
        if not CLAUDE_CONFIG_PATH.exists():
            raise ClaudeServiceError(
                message="Claude daemon not configured",
                code="NOT_CONFIGURED",
                suggestion="Run 'hostkit claude setup' first",
            )

        # Check project exists
        db = self._get_db()
        project = db.get_project(project_name)
        if not project:
            raise ClaudeServiceError(
                message=f"Project '{project_name}' not found",
                code="PROJECT_NOT_FOUND",
            )

        # Generate API key
        random_part = secrets.token_urlsafe(24)
        api_key = f"ck_{project_name}_{random_part}"
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        api_key_prefix = f"ck_{project_name}_{random_part[:8]}"

        # Insert into daemon database
        self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-c",
            f"""
            INSERT INTO project_keys (project_name, api_key_hash, api_key_prefix, enabled)
            VALUES ('{project_name}', '{api_key_hash}', '{api_key_prefix}', true)
            ON CONFLICT (project_name)
            DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash,
                          api_key_prefix = EXCLUDED.api_key_prefix,
                          enabled = true,
                          updated_at = NOW()
            """
        ])

        return ProjectKeyResult(
            project_name=project_name,
            api_key=api_key,
            api_key_prefix=api_key_prefix,
            endpoint="http://127.0.0.1:9000",
        )

    def disable_project(self, project_name: str) -> dict:
        """Disable Claude for a project.

        Revokes the API key and removes access.

        Args:
            project_name: Name of the project

        Returns:
            Dict with disable status

        Raises:
            ClaudeServiceError: If disabling fails
        """
        # Disable in daemon database
        self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-c",
            f"UPDATE project_keys SET enabled = false WHERE project_name = '{project_name}'"
        ], check=False)

        return {
            "project": project_name,
            "status": "disabled",
        }

    def grant_tools(self, project_name: str, tools: list[str], granted_by: str) -> dict:
        """Grant tools to a project.

        Args:
            project_name: Name of the project
            tools: List of tool names to grant
            granted_by: Username granting the tools

        Returns:
            Dict with granted tools

        Raises:
            ClaudeServiceError: If granting fails
        """
        # Validate tools
        valid_tools = {
            "logs", "health", "db:read", "db:write",
            "env:read", "env:write", "service",
            "deploy", "rollback", "migrate",
            "vector:search", "cache:flush",
        }

        invalid = set(tools) - valid_tools
        if invalid:
            raise ClaudeServiceError(
                message=f"Invalid tools: {', '.join(invalid)}",
                code="INVALID_TOOLS",
                suggestion=f"Valid tools: {', '.join(sorted(valid_tools))}",
            )

        # Grant tools in daemon database
        for tool in tools:
            self._run([
                "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-c",
                f"""
                INSERT INTO tool_permissions (project_name, tool_name, granted_by)
                VALUES ('{project_name}', '{tool}', '{granted_by}')
                ON CONFLICT (project_name, tool_name) DO NOTHING
                """
            ])

        return {
            "project": project_name,
            "granted": tools,
        }

    def revoke_tools(self, project_name: str, tools: list[str]) -> dict:
        """Revoke tools from a project.

        Args:
            project_name: Name of the project
            tools: List of tool names to revoke

        Returns:
            Dict with revoked tools
        """
        for tool in tools:
            self._run([
                "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-c",
                f"DELETE FROM tool_permissions WHERE project_name = '{project_name}' AND tool_name = '{tool}'"
            ], check=False)

        return {
            "project": project_name,
            "revoked": tools,
        }

    def list_tools(self, project_name: str) -> dict:
        """List granted tools for a project.

        Args:
            project_name: Name of the project

        Returns:
            Dict with tool list
        """
        result = self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-tAc",
            f"SELECT tool_name FROM tool_permissions WHERE project_name = '{project_name}' ORDER BY tool_name"
        ], check=False)

        tools = []
        if result.returncode == 0 and result.stdout.strip():
            tools = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]

        return {
            "project": project_name,
            "tools": tools,
        }

    def get_usage(self, project_name: str, detailed: bool = False) -> dict:
        """Get usage statistics for a project.

        Args:
            project_name: Name of the project
            detailed: Include per-conversation breakdown

        Returns:
            Dict with usage statistics
        """
        # Get today's usage
        result = self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-tAc",
            f"""
            SELECT COALESCE(SUM(requests), 0),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(tool_calls), 0)
            FROM usage_tracking
            WHERE project_name = '{project_name}' AND date = CURRENT_DATE
            """
        ], check=False)

        today = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("|")
            if len(parts) == 4:
                today = {
                    "requests": int(parts[0].strip() or 0),
                    "input_tokens": int(parts[1].strip() or 0),
                    "output_tokens": int(parts[2].strip() or 0),
                    "tool_calls": int(parts[3].strip() or 0),
                }

        # Get monthly usage
        result = self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-tAc",
            f"""
            SELECT COALESCE(SUM(requests), 0),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(tool_calls), 0)
            FROM usage_tracking
            WHERE project_name = '{project_name}'
              AND date >= date_trunc('month', CURRENT_DATE)
            """
        ], check=False)

        this_month = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("|")
            if len(parts) == 4:
                this_month = {
                    "requests": int(parts[0].strip() or 0),
                    "input_tokens": int(parts[1].strip() or 0),
                    "output_tokens": int(parts[2].strip() or 0),
                    "tool_calls": int(parts[3].strip() or 0),
                }

        # Get limits
        result = self._run([
            "sudo", "-u", "postgres", "psql", "-d", CLAUDE_DB_NAME, "-tAc",
            f"""
            SELECT rate_limit_rpm, daily_token_limit
            FROM project_keys
            WHERE project_name = '{project_name}'
            """
        ], check=False)

        limits = {"rate_limit_rpm": 60, "daily_token_limit": 1000000}
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("|")
            if len(parts) == 2:
                limits = {
                    "rate_limit_rpm": int(parts[0].strip() or 60),
                    "daily_token_limit": int(parts[1].strip() or 1000000),
                }

        # Calculate remaining
        used_today = today["input_tokens"] + today["output_tokens"]
        remaining = max(0, limits["daily_token_limit"] - used_today)

        return {
            "project": project_name,
            "today": today,
            "this_month": this_month,
            "limits": {
                **limits,
                "remaining_tokens": remaining,
            },
        }

"""Environment variable management service for HostKit."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hostkit.database import get_db

# Patterns for detecting sensitive values
SECRET_PATTERNS = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"api_key", re.IGNORECASE),
    re.compile(r"apikey", re.IGNORECASE),
    re.compile(r"private", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
]


@dataclass
class EnvVar:
    """Environment variable with optional redaction."""

    key: str
    value: str
    is_secret: bool = False

    def display_value(self, show_secrets: bool = False) -> str:
        """Get the display value, optionally redacted."""
        if self.is_secret and not show_secrets:
            return "********"
        return self.value


class EnvServiceError(Exception):
    """Exception for environment service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class EnvService:
    """Service for managing project environment variables."""

    def __init__(self) -> None:
        self.db = get_db()

    def _env_file_path(self, project: str) -> Path:
        """Get the path to a project's .env file."""
        return Path(f"/home/{project}/.env")

    def _is_secret_key(self, key: str) -> bool:
        """Check if a key name suggests it contains sensitive data."""
        for pattern in SECRET_PATTERNS:
            if pattern.search(key):
                return True
        return False

    def _parse_env_file(self, content: str) -> dict[str, str]:
        """Parse .env file content into a dictionary.

        Handles:
        - KEY=VALUE
        - KEY="VALUE WITH SPACES"
        - KEY='VALUE WITH SPACES'
        - Comments (# ...)
        - Empty lines
        """
        env_vars: dict[str, str] = {}

        for line in content.splitlines():
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Split on first = sign
            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()

            if not key:
                continue

            # Remove surrounding quotes if present
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]

            env_vars[key] = value

        return env_vars

    def _format_env_file(self, env_vars: dict[str, str]) -> str:
        """Format environment variables as .env file content.

        Preserves ordering and quotes values with spaces.
        """
        lines = []
        for key, value in env_vars.items():
            # Quote values that contain spaces or special characters
            if " " in value or "'" in value or '"' in value or "#" in value:
                # Use double quotes and escape internal double quotes
                escaped_value = value.replace('"', '\\"')
                lines.append(f'{key}="{escaped_value}"')
            else:
                lines.append(f"{key}={value}")
        return "\n".join(lines) + "\n"

    def _validate_project(self, project: str) -> None:
        """Validate that the project exists."""
        if not self.db.get_project(project):
            raise EnvServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def _read_env_file(self, project: str) -> dict[str, str]:
        """Read and parse the project's .env file."""
        env_path = self._env_file_path(project)
        if not env_path.exists():
            return {}
        return self._parse_env_file(env_path.read_text())

    def _write_env_file(self, project: str, env_vars: dict[str, str]) -> None:
        """Write environment variables to the project's .env file."""
        import subprocess

        env_path = self._env_file_path(project)
        content = self._format_env_file(env_vars)
        env_path.write_text(content)

        # Set correct ownership and permissions
        subprocess.run(["chown", f"{project}:{project}", str(env_path)], check=True)
        subprocess.run(["chmod", "600", str(env_path)], check=True)

    def list_env(self, project: str, show_secrets: bool = False) -> list[dict[str, Any]]:
        """List all environment variables for a project.

        Args:
            project: Project name
            show_secrets: If True, show actual secret values; otherwise redact

        Returns:
            List of dicts with key, value, is_secret fields
        """
        self._validate_project(project)

        env_vars = self._read_env_file(project)
        result = []

        for key, value in env_vars.items():
            is_secret = self._is_secret_key(key)
            env_var = EnvVar(key=key, value=value, is_secret=is_secret)
            result.append(
                {
                    "key": key,
                    "value": env_var.display_value(show_secrets),
                    "is_secret": is_secret,
                }
            )

        return result

    def get_env(self, project: str, key: str) -> str | None:
        """Get a specific environment variable value.

        Args:
            project: Project name
            key: Variable name

        Returns:
            The value if found, None otherwise
        """
        self._validate_project(project)
        env_vars = self._read_env_file(project)
        return env_vars.get(key)

    def set_env(self, project: str, key: str, value: str) -> dict[str, Any]:
        """Set an environment variable.

        Args:
            project: Project name
            key: Variable name
            value: Variable value

        Returns:
            Dict with the operation result
        """
        self._validate_project(project)

        # Validate key format
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise EnvServiceError(
                code="INVALID_KEY",
                message=f"Invalid environment variable name: {key}",
                suggestion="Variable names must start with a letter or underscore, "
                "and contain only letters, numbers, and underscores",
            )

        env_vars = self._read_env_file(project)
        was_set = key in env_vars
        env_vars[key] = value
        self._write_env_file(project, env_vars)

        return {
            "project": project,
            "key": key,
            "action": "updated" if was_set else "created",
        }

    def unset_env(self, project: str, key: str) -> dict[str, Any]:
        """Remove an environment variable.

        Args:
            project: Project name
            key: Variable name

        Returns:
            Dict with the operation result
        """
        self._validate_project(project)

        env_vars = self._read_env_file(project)
        if key not in env_vars:
            raise EnvServiceError(
                code="VAR_NOT_FOUND",
                message=f"Environment variable '{key}' not found",
                suggestion=f"Run 'hostkit env {project}' to see available variables",
            )

        del env_vars[key]
        self._write_env_file(project, env_vars)

        return {
            "project": project,
            "key": key,
            "action": "removed",
        }

    def import_env(self, project: str, file_path: str) -> dict[str, Any]:
        """Import environment variables from a file, replacing all existing.

        Args:
            project: Project name
            file_path: Path to the source .env file

        Returns:
            Dict with the operation result
        """
        self._validate_project(project)

        source_path = Path(file_path)
        if not source_path.exists():
            raise EnvServiceError(
                code="FILE_NOT_FOUND",
                message=f"File not found: {file_path}",
                suggestion="Check the file path and try again",
            )

        new_vars = self._parse_env_file(source_path.read_text())
        self._write_env_file(project, new_vars)

        return {
            "project": project,
            "action": "imported",
            "variables_count": len(new_vars),
            "source": file_path,
        }

    def sync_env(self, project: str, file_path: str) -> dict[str, Any]:
        """Merge environment variables from a file (no overwrite).

        Only adds new variables, does not overwrite existing ones.

        Args:
            project: Project name
            file_path: Path to the source .env file

        Returns:
            Dict with the operation result
        """
        self._validate_project(project)

        source_path = Path(file_path)
        if not source_path.exists():
            raise EnvServiceError(
                code="FILE_NOT_FOUND",
                message=f"File not found: {file_path}",
                suggestion="Check the file path and try again",
            )

        existing_vars = self._read_env_file(project)
        new_vars = self._parse_env_file(source_path.read_text())

        added = []
        skipped = []

        for key, value in new_vars.items():
            if key in existing_vars:
                skipped.append(key)
            else:
                existing_vars[key] = value
                added.append(key)

        self._write_env_file(project, existing_vars)

        return {
            "project": project,
            "action": "synced",
            "added_count": len(added),
            "skipped_count": len(skipped),
            "added": added,
            "skipped": skipped,
            "source": file_path,
        }

    def capture_snapshot(self, project: str) -> str:
        """Capture current environment as a JSON snapshot.

        Args:
            project: Project name

        Returns:
            JSON string of environment variables
        """
        self._validate_project(project)
        env_vars = self._read_env_file(project)
        return json.dumps(env_vars, sort_keys=True)

    def restore_snapshot(self, project: str, snapshot: str) -> dict[str, Any]:
        """Restore environment from a JSON snapshot.

        Args:
            project: Project name
            snapshot: JSON string of environment variables

        Returns:
            Dict with the operation result
        """
        self._validate_project(project)

        try:
            env_vars = json.loads(snapshot)
        except json.JSONDecodeError as e:
            raise EnvServiceError(
                code="INVALID_SNAPSHOT",
                message=f"Invalid environment snapshot: {e}",
                suggestion="The snapshot may be corrupted",
            )

        if not isinstance(env_vars, dict):
            raise EnvServiceError(
                code="INVALID_SNAPSHOT",
                message="Environment snapshot must be a JSON object",
                suggestion="The snapshot format is invalid",
            )

        self._write_env_file(project, env_vars)

        return {
            "project": project,
            "action": "restored",
            "variables_count": len(env_vars),
        }

    def compare_snapshot(self, project: str, snapshot: str) -> dict[str, Any]:
        """Compare current environment with a snapshot.

        Args:
            project: Project name
            snapshot: JSON string of environment variables to compare against

        Returns:
            Dict with added, removed, and changed variables
        """
        self._validate_project(project)

        try:
            snapshot_vars = json.loads(snapshot)
        except json.JSONDecodeError as e:
            raise EnvServiceError(
                code="INVALID_SNAPSHOT",
                message=f"Invalid environment snapshot: {e}",
                suggestion="The snapshot may be corrupted",
            )

        if not isinstance(snapshot_vars, dict):
            raise EnvServiceError(
                code="INVALID_SNAPSHOT",
                message="Environment snapshot must be a JSON object",
                suggestion="The snapshot format is invalid",
            )

        current_vars = self._read_env_file(project)

        added = []  # In current but not in snapshot
        removed = []  # In snapshot but not in current
        changed = []  # Different values

        snapshot_keys = set(snapshot_vars.keys())
        current_keys = set(current_vars.keys())

        for key in current_keys - snapshot_keys:
            added.append(key)

        for key in snapshot_keys - current_keys:
            removed.append(key)

        for key in snapshot_keys & current_keys:
            if snapshot_vars[key] != current_vars[key]:
                changed.append(
                    {
                        "key": key,
                        "snapshot_value": snapshot_vars[key]
                        if not self._is_secret_key(key)
                        else "********",
                        "current_value": current_vars[key]
                        if not self._is_secret_key(key)
                        else "********",
                    }
                )

        return {
            "project": project,
            "has_changes": bool(added or removed or changed),
            "added": added,
            "removed": removed,
            "changed": changed,
        }

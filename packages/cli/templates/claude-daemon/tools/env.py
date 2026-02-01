"""Environment tools - Read and write project environment variables."""

import asyncio
import logging
import os
import re
from typing import Any

from tools.base import BaseTool, ToolResult, ToolTier

logger = logging.getLogger(__name__)


# Patterns for sensitive keys that should be redacted in output
SENSITIVE_PATTERNS = [
    re.compile(r".*_KEY$", re.IGNORECASE),
    re.compile(r".*_SECRET$", re.IGNORECASE),
    re.compile(r".*_TOKEN$", re.IGNORECASE),
    re.compile(r".*_PASSWORD$", re.IGNORECASE),
    re.compile(r".*_API_KEY$", re.IGNORECASE),
    re.compile(r".*_PRIVATE.*", re.IGNORECASE),
    re.compile(r"^SECRET.*", re.IGNORECASE),
    re.compile(r"^PASSWORD$", re.IGNORECASE),
    re.compile(r"^PASS$", re.IGNORECASE),
    re.compile(r".*_CREDENTIALS?$", re.IGNORECASE),
]

# Database URL patterns that contain credentials
DATABASE_URL_PATTERNS = [
    re.compile(r".*DATABASE.*URL.*", re.IGNORECASE),
    re.compile(r".*_DB_URL$", re.IGNORECASE),
    re.compile(r"^DB_URL$", re.IGNORECASE),
    re.compile(r".*REDIS.*URL.*", re.IGNORECASE),
    re.compile(r".*MONGO.*URL.*", re.IGNORECASE),
]


def is_sensitive_key(key: str) -> bool:
    """Check if a key should have its value redacted."""
    for pattern in SENSITIVE_PATTERNS:
        if pattern.match(key):
            return True
    for pattern in DATABASE_URL_PATTERNS:
        if pattern.match(key):
            return True
    return False


def parse_env_file(content: str) -> dict[str, str]:
    """Parse .env file content into key-value pairs.

    Handles:
    - KEY=value
    - KEY="value with spaces"
    - KEY='value with spaces'
    - Comments (#)
    - Empty lines
    """
    env_vars = {}
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
        value = value.strip()

        # Remove surrounding quotes
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        if key:
            env_vars[key] = value

    return env_vars


class EnvReadTool(BaseTool):
    """Read environment variables from a project's .env file.

    Tier 1 (read-only): Safe to use without confirmation.

    Sensitive values (passwords, secrets, tokens, API keys) are automatically
    redacted in the output, but the actual values are available in the
    structured data for programmatic use.
    """

    name = "env_read"
    description = (
        "Read environment variables from the project's .env file. "
        "Sensitive values are automatically redacted in the display output."
    )
    tier = ToolTier.READ_ONLY

    input_schema = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific keys to read (optional, reads all if omitted)",
            },
        },
        "required": [],
    }

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Read environment variables from project .env file.

        Args:
            project_name: Project whose environment to read
            keys: Optional list of specific keys to read

        Returns:
            ToolResult with environment variables (sensitive values redacted)
        """
        keys_filter = params.get("keys", None)
        env_path = f"/home/{project_name}/.env"

        try:
            # Read the .env file
            if not os.path.exists(env_path):
                return ToolResult(
                    success=True,
                    output=f"No .env file found at {env_path}",
                    data={"variables": {}, "count": 0},
                )

            with open(env_path, "r") as f:
                content = f.read()

            env_vars = parse_env_file(content)

            # Filter if keys specified
            if keys_filter:
                env_vars = {k: v for k, v in env_vars.items() if k in keys_filter}

            # Build redacted output for display
            output_lines = []
            for key, value in sorted(env_vars.items()):
                if is_sensitive_key(key):
                    output_lines.append(f"{key}=***REDACTED***")
                else:
                    # Truncate long values in display
                    display_value = value if len(value) <= 100 else value[:97] + "..."
                    output_lines.append(f"{key}={display_value}")

            output = "\n".join(output_lines) if output_lines else "No environment variables found."

            return ToolResult(
                success=True,
                output=output,
                data={
                    "variables": env_vars,  # Full values for programmatic use
                    "count": len(env_vars),
                    "sensitive_keys": [k for k in env_vars if is_sensitive_key(k)],
                },
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied reading {env_path}",
            )
        except Exception as e:
            logger.exception(f"Failed to read env for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )


class EnvWriteTool(BaseTool):
    """Set environment variables in a project's .env file.

    Tier 2 (state-change): Modifies project configuration.

    Changes require a service restart to take effect.
    """

    name = "env_write"
    description = (
        "Set environment variables in the project's .env file. "
        "Note: You must restart the service for changes to take effect."
    )
    tier = ToolTier.STATE_CHANGE

    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Environment variable key (for single variable)",
            },
            "value": {
                "type": "string",
                "description": "Environment variable value (for single variable)",
            },
            "variables": {
                "type": "object",
                "description": "Multiple key-value pairs to set",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": [],
    }

    # Valid key pattern (alphanumeric and underscore, starts with letter/underscore)
    KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Set environment variables in project .env file.

        Args:
            project_name: Project whose environment to modify
            key: Single key to set
            value: Value for single key
            variables: Dict of multiple key-value pairs

        Returns:
            ToolResult with updated variable info
        """
        env_path = f"/home/{project_name}/.env"

        # Collect variables to set
        variables_to_set: dict[str, str] = {}

        # Single key=value
        if params.get("key"):
            key = params["key"]
            value = params.get("value", "")

            if not self.KEY_PATTERN.match(key):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Invalid key format: '{key}'. Keys must be alphanumeric with underscores, starting with a letter or underscore.",
                )

            variables_to_set[key] = value

        # Multiple variables
        if params.get("variables"):
            for key, value in params["variables"].items():
                if not self.KEY_PATTERN.match(key):
                    return ToolResult(
                        success=False,
                        output="",
                        error=f"Invalid key format: '{key}'. Keys must be alphanumeric with underscores.",
                    )
                variables_to_set[key] = str(value)

        if not variables_to_set:
            return ToolResult(
                success=False,
                output="",
                error="No variables specified. Provide 'key' and 'value', or 'variables' dict.",
            )

        try:
            # Read existing .env
            existing_vars = {}
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    content = f.read()
                existing_vars = parse_env_file(content)

            # Merge new variables
            updated_vars = {**existing_vars, **variables_to_set}

            # Write back
            lines = []
            for key in sorted(updated_vars.keys()):
                value = updated_vars[key]
                # Quote values with spaces or special chars
                if " " in value or '"' in value or "'" in value or "=" in value:
                    # Escape existing quotes
                    value = value.replace('"', '\\"')
                    lines.append(f'{key}="{value}"')
                else:
                    lines.append(f"{key}={value}")

            with open(env_path, "w") as f:
                f.write("\n".join(lines) + "\n")

            # Build output showing what was set (with redaction)
            output_lines = [f"Updated {len(variables_to_set)} variable(s) in {env_path}:"]
            for key in sorted(variables_to_set.keys()):
                if is_sensitive_key(key):
                    output_lines.append(f"  {key}=***REDACTED***")
                else:
                    value = variables_to_set[key]
                    display = value if len(value) <= 50 else value[:47] + "..."
                    output_lines.append(f"  {key}={display}")

            output_lines.append("")
            output_lines.append("⚠️  Remember to restart the service for changes to take effect:")
            output_lines.append(f"    hostkit service restart {project_name}")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                data={
                    "updated_keys": list(variables_to_set.keys()),
                    "total_variables": len(updated_vars),
                    "requires_restart": True,
                },
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied writing to {env_path}",
            )
        except Exception as e:
            logger.exception(f"Failed to write env for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

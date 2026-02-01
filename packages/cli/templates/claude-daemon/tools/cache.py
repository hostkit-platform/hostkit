"""Cache tool - Flush Redis cache for projects."""

import asyncio
import logging
import re
from typing import Any

from tools.base import BaseTool, ToolResult, ToolTier

logger = logging.getLogger(__name__)


class CacheFlushTool(BaseTool):
    """Flush Redis cache for a project.

    Tier 2 (state-change): Modifies cache state.

    Projects using Redis have keys namespaced by project name.
    This tool flushes all keys for the project, or keys matching
    a specific pattern.
    """

    name = "cache_flush"
    description = (
        "Flush the project's Redis cache. Optionally specify a pattern "
        "to flush specific keys only."
    )
    tier = ToolTier.STATE_CHANGE

    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Key pattern to flush (optional, uses project:* if omitted). Supports glob patterns like 'session:*' or 'user:123:*'.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm the flush operation",
                "default": False,
            },
        },
        "required": [],
    }

    # Validate pattern to prevent command injection
    SAFE_PATTERN = re.compile(r"^[a-zA-Z0-9_:\-\*\?]+$")

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Flush Redis cache for the project.

        Args:
            project_name: Project whose cache to flush
            pattern: Optional key pattern (default: project:*)
            confirm: Must be True to proceed

        Returns:
            ToolResult with flush result
        """
        user_pattern = params.get("pattern", "")
        confirm = params.get("confirm", False)

        # Build the full pattern (project-namespaced)
        if user_pattern:
            # Validate pattern
            if not self.SAFE_PATTERN.match(user_pattern):
                return ToolResult(
                    success=False,
                    output="",
                    error="Invalid pattern. Patterns can only contain alphanumeric characters, underscores, colons, hyphens, and wildcards (* or ?).",
                )
            full_pattern = f"{project_name}:{user_pattern}"
        else:
            full_pattern = f"{project_name}:*"

        # First, count matching keys
        try:
            count_result = await self._count_keys(full_pattern)
            if not count_result.success:
                return count_result

            key_count = count_result.data.get("count", 0)

            if key_count == 0:
                return ToolResult(
                    success=True,
                    output=f"No keys found matching pattern '{full_pattern}'. Cache is already empty for this pattern.",
                    data={"pattern": full_pattern, "flushed_count": 0},
                )

            # If not confirmed and there are keys, ask for confirmation
            if not confirm:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Found {key_count} key(s) matching '{full_pattern}'. Set confirm=true to proceed with flush.",
                    data={"pattern": full_pattern, "key_count": key_count, "requires_confirmation": True},
                )

            # Proceed with flush
            return await self._flush_keys(full_pattern, key_count)

        except Exception as e:
            logger.exception(f"Cache flush failed for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

    async def _count_keys(self, pattern: str) -> ToolResult:
        """Count keys matching pattern."""
        # Use redis-cli to count keys
        process = await asyncio.create_subprocess_exec(
            "redis-cli",
            "KEYS",
            pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()

            # Check for common Redis errors
            if "connection refused" in error.lower():
                return ToolResult(
                    success=False,
                    output="",
                    error="Redis connection refused. Is Redis running?",
                )

            return ToolResult(
                success=False,
                output="",
                error=f"Redis error: {error}",
            )

        # Count lines (each line is a key)
        output = stdout.decode().strip()
        keys = [k for k in output.splitlines() if k.strip()]
        count = len(keys)

        return ToolResult(
            success=True,
            output=f"Found {count} key(s) matching pattern.",
            data={"count": count, "keys": keys[:100]},  # Limit key list
        )

    async def _flush_keys(self, pattern: str, expected_count: int) -> ToolResult:
        """Flush keys matching pattern using DEL command with KEYS."""
        # Use eval script for atomic delete
        # KEYS pattern, then delete all matching
        lua_script = """
        local keys = redis.call('KEYS', ARGV[1])
        local count = 0
        for i, key in ipairs(keys) do
            redis.call('DEL', key)
            count = count + 1
        end
        return count
        """

        process = await asyncio.create_subprocess_exec(
            "redis-cli",
            "EVAL",
            lua_script,
            "0",  # No keys in KEYS array
            pattern,  # Pattern as ARGV[1]
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            return ToolResult(
                success=False,
                output="",
                error=f"Flush failed: {error}",
            )

        # Parse count from output
        output = stdout.decode().strip()
        try:
            deleted_count = int(output)
        except ValueError:
            deleted_count = expected_count  # Fallback

        return ToolResult(
            success=True,
            output=f"Successfully flushed {deleted_count} key(s) matching pattern '{pattern}'.",
            data={
                "pattern": pattern,
                "flushed_count": deleted_count,
            },
        )

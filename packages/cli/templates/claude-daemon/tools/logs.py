"""Logs tool - Read project application logs."""

import asyncio
import logging
from typing import Any

from tools.base import BaseTool, ToolResult, ToolTier

logger = logging.getLogger(__name__)


class LogsTool(BaseTool):
    """Read project application logs.

    Tier 1 (read-only): Safe to use without confirmation.

    Uses journalctl to read systemd service logs for the project.
    Supports filtering by:
    - Number of lines
    - Time range (since)
    - Log level
    - Search pattern
    """

    name = "logs"
    description = (
        "Read project application logs. Use this to diagnose errors, "
        "check application behavior, and monitor activity."
    )
    tier = ToolTier.READ_ONLY

    input_schema = {
        "type": "object",
        "properties": {
            "lines": {
                "type": "integer",
                "description": "Number of log lines to retrieve (default: 100, max: 1000)",
                "default": 100,
            },
            "since": {
                "type": "string",
                "description": "Retrieve logs since this time (e.g., '1h', '30m', '2024-01-01')",
            },
            "level": {
                "type": "string",
                "enum": ["debug", "info", "warning", "error"],
                "description": "Minimum log level to retrieve",
            },
            "search": {
                "type": "string",
                "description": "Search pattern to filter logs (grep-style)",
            },
        },
    }

    # Map log levels to journalctl priorities
    LEVEL_PRIORITIES = {
        "debug": 7,
        "info": 6,
        "warning": 4,
        "error": 3,
    }

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Read logs for the project.

        Args:
            project_name: Project to read logs for
            lines: Number of lines (default 100, max 1000)
            since: Time filter (e.g., "1h", "30m")
            level: Minimum log level
            search: Search pattern

        Returns:
            ToolResult with log content
        """
        lines = min(params.get("lines", 100), 1000)
        since = params.get("since")
        level = params.get("level")
        search = params.get("search")

        # Build journalctl command
        service_name = f"hostkit-{project_name}"
        cmd = ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"]

        # Add time filter
        if since:
            cmd.extend(["--since", since])

        # Add priority filter
        if level and level in self.LEVEL_PRIORITIES:
            priority = self.LEVEL_PRIORITIES[level]
            cmd.extend(["-p", str(priority)])

        try:
            # Execute journalctl
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_output = stderr.decode().strip()

                # Check for common errors
                if "No journal files were found" in error_output:
                    return ToolResult(
                        success=True,
                        output="No logs found for this service.",
                        data={"lines": 0, "service": service_name},
                    )

                return ToolResult(
                    success=False,
                    output="",
                    error=f"Failed to read logs: {error_output}",
                )

            output = stdout.decode()

            # Apply search filter if provided
            if search and output:
                lines_list = output.splitlines()
                filtered = [
                    line for line in lines_list if search.lower() in line.lower()
                ]
                output = "\n".join(filtered)
                if not filtered:
                    output = f"No log entries matching '{search}'"

            # Truncate if needed
            output, truncated = self.truncate_output(output)

            # Count lines for metadata
            line_count = len(output.splitlines()) if output else 0

            return ToolResult(
                success=True,
                output=output or "No log entries found.",
                data={
                    "lines": line_count,
                    "service": service_name,
                    "filtered": bool(search),
                },
                truncated=truncated,
            )

        except Exception as e:
            logger.exception(f"Error reading logs for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

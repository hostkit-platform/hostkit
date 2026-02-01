"""Health tool - Check service health status."""

import asyncio
import logging
import time
from typing import Any

import httpx

from tools.base import BaseTool, ToolResult, ToolTier

logger = logging.getLogger(__name__)


class HealthTool(BaseTool):
    """Check the health status of a project's service.

    Tier 1 (read-only): Safe to use without confirmation.

    Makes an HTTP request to the project's health endpoint and reports:
    - HTTP status code
    - Response time
    - Response body (if small)
    - Any errors
    """

    name = "health"
    description = (
        "Check the health status of the project's service. "
        "Returns HTTP status, response time, and any errors."
    )
    tier = ToolTier.READ_ONLY

    input_schema = {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "description": "Health check endpoint path (default: /health)",
                "default": "/health",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds (default: 10, max: 30)",
                "default": 10,
            },
        },
    }

    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Check health of the project's service.

        Args:
            project_name: Project to check
            endpoint: Health endpoint path (default: /health)
            timeout: Request timeout in seconds

        Returns:
            ToolResult with health status
        """
        endpoint = params.get("endpoint", "/health")
        timeout = min(params.get("timeout", 10), 30)

        # Ensure endpoint starts with /
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        # First, get the project's port from the database or config
        # For now, use internal service URL pattern
        # Projects run on ports starting at 8000 + offset
        # We'll query this via hostkit CLI
        try:
            port = await self._get_project_port(project_name)
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Could not determine project port: {e}",
            )

        url = f"http://127.0.0.1:{port}{endpoint}"

        try:
            start_time = time.time()

            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=timeout)

            duration_ms = int((time.time() - start_time) * 1000)

            # Determine health status
            is_healthy = 200 <= response.status_code < 300

            # Build output
            status_emoji = "healthy" if is_healthy else "unhealthy"
            output_lines = [
                f"Status: {status_emoji}",
                f"HTTP Status: {response.status_code}",
                f"Response Time: {duration_ms}ms",
            ]

            # Include response body if small
            body = response.text
            if len(body) <= 1000:
                output_lines.append(f"Response: {body}")
            elif body:
                output_lines.append(f"Response: {body[:500]}... (truncated)")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                data={
                    "healthy": is_healthy,
                    "status_code": response.status_code,
                    "response_time_ms": duration_ms,
                    "endpoint": endpoint,
                    "url": url,
                },
            )

        except httpx.TimeoutException:
            return ToolResult(
                success=True,  # Tool succeeded, but service is unhealthy
                output=f"Status: unhealthy\nError: Request timed out after {timeout}s",
                data={
                    "healthy": False,
                    "error": "timeout",
                    "endpoint": endpoint,
                },
            )

        except httpx.ConnectError as e:
            return ToolResult(
                success=True,
                output=f"Status: unhealthy\nError: Could not connect to service\nDetails: {e}",
                data={
                    "healthy": False,
                    "error": "connection_failed",
                    "endpoint": endpoint,
                },
            )

        except Exception as e:
            logger.exception(f"Error checking health for {project_name}")
            return ToolResult(
                success=False,
                output="",
                error=self.format_error(e),
            )

    async def _get_project_port(self, project_name: str) -> int:
        """Get the port number for a project.

        Queries hostkit project info to get the assigned port.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "hostkit",
                "project",
                "info",
                project_name,
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise RuntimeError(f"hostkit project info failed: {stderr.decode()}")

            import json

            data = json.loads(stdout.decode())

            if not data.get("success"):
                raise RuntimeError(data.get("error", {}).get("message", "Unknown error"))

            return data["data"]["port"]

        except Exception as e:
            logger.warning(f"Could not get port for {project_name}: {e}")
            # Fallback: try to determine from systemd service
            return await self._get_port_from_systemd(project_name)

    async def _get_port_from_systemd(self, project_name: str) -> int:
        """Fallback: extract port from systemd service file."""
        service_file = f"/etc/systemd/system/hostkit-{project_name}.service"

        try:
            process = await asyncio.create_subprocess_exec(
                "grep",
                "-oP",
                r"--port\s+\K\d+",
                service_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()

            if process.returncode == 0 and stdout:
                return int(stdout.decode().strip())

        except Exception:
            pass

        # Ultimate fallback - use convention
        # This would need to be improved for production
        raise RuntimeError("Could not determine service port")

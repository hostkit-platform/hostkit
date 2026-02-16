"""Request logging middleware for auth service."""

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all HTTP requests and responses with timing.

    Enabled when DEBUG_REQUESTS=true environment variable is set.
    Logs request method/path on entry and response status/duration on exit.
    """

    async def dispatch(self, request: Request, call_next):
        """Log incoming request and outgoing response."""
        start_time = time.time()
        request_id = request.headers.get("x-request-id", "")
        id_suffix = f" ({request_id})" if request_id else ""

        # Log incoming request
        logger.info(f"→ {request.method} {request.url.path}{id_suffix}")

        try:
            response = await call_next(request)
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"✗ {request.method} {request.url.path} - ERROR ({duration_ms:.2f}ms){id_suffix}",
                exc_info=e,
            )
            raise

        # Log response
        duration_ms = (time.time() - start_time) * 1000
        status_code = response.status_code

        # Use different symbols based on status code
        if status_code < 300:
            symbol = "✓"
        elif status_code < 400:
            symbol = "→"
        elif status_code < 500:
            symbol = "⚠"
        else:
            symbol = "✗"

        logger.info(f"{symbol} {request.method} {request.url.path} - {status_code} ({duration_ms:.2f}ms){id_suffix}")

        return response

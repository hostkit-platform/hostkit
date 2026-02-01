"""HostKit OAuth Proxy - Central OAuth Service.

Stateless OAuth proxy service that handles OAuth authentication
for all HostKit projects via a single callback URL.

Flow:
1. Project frontend redirects to auth.hostkit.dev/oauth/{provider}/start
2. Central auth redirects to provider (Google/Apple)
3. Provider authenticates and redirects back to central auth
4. Central auth validates token, creates signed identity payload
5. Redirects to project's return_url with identity payload
6. Project verifies RSA signature and creates/links user

This service has NO database - all state is stored in encrypted
OAuth state parameters.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from services.signing import get_signing_service

# Configure logging based on LOG_LEVEL env var
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set uvicorn loggers to same level
logging.getLogger("uvicorn").setLevel(getattr(logging, log_level, logging.INFO))
logging.getLogger("uvicorn.error").setLevel(getattr(logging, log_level, logging.INFO))
logging.getLogger("uvicorn.access").setLevel(getattr(logging, log_level, logging.INFO))

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Loads RSA keys on startup for identity payload signing.
    """
    settings = get_settings()
    logger.info("Starting HostKit OAuth Proxy")
    logger.info(f"Base URL: {settings.base_url}")
    logger.info(f"Listening on port: {settings.oauth_port}")
    logger.info(f"Log level: {log_level}")

    # Load signing keys
    signing_service = get_signing_service()
    try:
        signing_service.load_keys()
        logger.info("RSA signing keys loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load RSA signing keys: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down OAuth Proxy")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="HostKit OAuth Proxy",
        description="Central OAuth proxy for all HostKit projects",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware - allow all hostkit.dev subdomains
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https://[\w-]+\.hostkit\.dev",
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Import and register routers
    from routers.google import router as google_router
    from routers.apple import router as apple_router
    from routers.keys import router as keys_router

    app.include_router(google_router)
    app.include_router(apple_router)
    app.include_router(keys_router)

    # Health endpoint
    @app.get("/health", tags=["health"])
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "service": "oauth-proxy",
            "version": "1.0.0",
        }

    return app


# Application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.oauth_port,
        reload=False,
    )

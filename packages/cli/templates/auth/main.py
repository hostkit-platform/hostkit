"""HostKit Auth Service - FastAPI Application.

Per-project authentication service providing:
- Email/password authentication
- OAuth (Google, Apple)
- Magic links (passwordless)
- Anonymous sessions
- JWT access/refresh tokens
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

from config import get_settings
from routers import (
    auth_router,
    oauth_router,
    magic_link_router,
    anonymous_router,
    token_router,
    user_router,
    health_router,
    identity_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Runs startup and shutdown logic.
    """
    # Startup
    settings = get_settings()
    logger.info(f"Starting auth service for project: {settings.project_name}")
    logger.info(f"Listening on port: {settings.auth_service_port}")
    logger.info(f"Log level: {log_level}")

    yield

    # Shutdown
    logger.info("Shutting down auth service")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=f"HostKit Auth - {settings.project_name}",
        description="Authentication service for HostKit projects",
        version="1.0.0",
        docs_url="/auth/docs",
        redoc_url="/auth/redoc",
        openapi_url="/auth/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware - configure for your domains
    cors_origins = [
        settings.base_url,
        "http://localhost:3000",  # Local development
    ]
    # Add additional origins from AUTH_CORS_ORIGINS env var
    if settings.auth_cors_origins:
        cors_origins.extend(
            origin.strip()
            for origin in settings.auth_cors_origins.split(",")
            if origin.strip()
        )
    logger.info(f"CORS origins: {cors_origins}")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(magic_link_router)
    app.include_router(anonymous_router)
    app.include_router(token_router)
    app.include_router(user_router)
    app.include_router(identity_router)

    return app


# Application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.auth_service_port,
        reload=False,
    )

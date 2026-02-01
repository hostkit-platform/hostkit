"""HostKit Chatbot Service - FastAPI Application.

Per-project AI chatbot service providing:
- Embeddable widget for any website
- Conversation history with session tracking
- SSE streaming responses
- Configurable LLM (Claude, GPT-4)
- CTA injection after N messages
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

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
    health_router,
    chat_router,
    conversations_router,
    widget_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Runs startup and shutdown logic.
    """
    # Startup
    settings = get_settings()
    logger.info(f"Starting chatbot service for project: {settings.project_name}")
    logger.info(f"Listening on port: {settings.chatbot_port}")
    logger.info(f"Log level: {log_level}")
    logger.info(f"LLM Provider: {settings.llm_provider}")
    logger.info(f"Model: {settings.llm_model}")

    yield

    # Shutdown
    logger.info("Shutting down chatbot service")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=f"HostKit Chatbot - {settings.project_name}",
        description="AI-powered chatbot service for HostKit projects",
        version="1.0.0",
        docs_url="/chatbot/docs",
        redoc_url="/chatbot/redoc",
        openapi_url="/chatbot/openapi.json",
        lifespan=lifespan,
    )

    # CORS middleware - allow widget embedding from any origin
    cors_origins = [
        settings.base_url,
        "http://localhost:3000",  # Local development
    ]
    # Add additional origins from CHATBOT_CORS_ORIGINS env var
    if settings.chatbot_cors_origins:
        cors_origins.extend(
            origin.strip()
            for origin in settings.chatbot_cors_origins.split(",")
            if origin.strip()
        )
    # Widget needs to work from any origin
    cors_origins.append("*")
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
    app.include_router(chat_router)
    app.include_router(conversations_router)
    app.include_router(widget_router)

    # Mount static files for widget assets
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/chatbot/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


# Application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.chatbot_port,
        reload=False,
    )

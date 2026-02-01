"""HostKit Claude Daemon - FastAPI Application.

A shared Claude daemon that enables projects to leverage AI capabilities
with the VPS owner's Claude/Anthropic subscription.

Features:
- Per-project API key authentication
- Tool execution with permission system
- Conversation persistence
- Usage tracking and rate limiting
"""

from contextlib import asynccontextmanager

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from middleware.rate_limit import RateLimitMiddleware

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
from database import init_db, close_db
from routers import (
    health_router,
    chat_router,
    conversations_router,
    tools_router,
    usage_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    print(f"Starting HostKit Claude Daemon on port {settings.PORT}")
    await init_db()
    yield
    # Shutdown
    print("Shutting down Claude Daemon")
    await close_db()


app = FastAPI(
    title="HostKit Claude Daemon",
    description="Shared Claude AI service for HostKit projects",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configured per-deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware
app.add_middleware(RateLimitMiddleware, global_rpm_limit=1000)

# Include routers
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(tools_router)
app.include_router(usage_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )

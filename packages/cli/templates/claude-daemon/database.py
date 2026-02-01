"""Database connection management for Claude daemon."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from models.base import Base


# Database engine (singleton)
_engine = None
_session_factory = None


async def init_db():
    """Initialize database connection and create tables."""
    global _engine, _session_factory

    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Create tables if they don't exist
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connection."""
    global _engine, _session_factory

    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session (FastAPI dependency)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

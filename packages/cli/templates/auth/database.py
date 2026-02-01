"""Database setup and session management.

Uses SQLAlchemy 2.0 async patterns with asyncpg.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# Create async engine
settings = get_settings()

# Convert postgresql:// to postgresql+asyncpg://
db_url = settings.auth_db_url
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    db_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Create session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session.

    Usage:
        @app.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables.

    Note: In production, tables are created by HostKit during 'auth enable'.
    This function is for development/testing only.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

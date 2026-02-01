"""Database connection management."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from config import settings
from models.base import ServiceBase, ProjectBase


# Service database engine (singleton)
_service_engine = None
_service_session_factory = None

# Project database engines (cached per project)
_project_engines: dict = {}
_project_session_factories: dict = {}


async def init_service_db():
    """Initialize service database connection."""
    global _service_engine, _service_session_factory

    _service_engine = create_async_engine(
        settings.SERVICE_DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    _service_session_factory = async_sessionmaker(
        _service_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Create tables if they don't exist
    async with _service_engine.begin() as conn:
        await conn.run_sync(ServiceBase.metadata.create_all)


async def close_service_db():
    """Close service database connection."""
    global _service_engine, _service_session_factory

    if _service_engine:
        await _service_engine.dispose()
        _service_engine = None
        _service_session_factory = None


async def get_service_session() -> AsyncGenerator[AsyncSession, None]:
    """Get service database session (FastAPI dependency)."""
    if _service_session_factory is None:
        raise RuntimeError("Service database not initialized")

    async with _service_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_project_database_url(project_name: str) -> str:
    """Get database URL for a project."""
    # Extract password from service URL
    # Format: postgresql+asyncpg://user:password@host/db
    password = settings.SERVICE_DATABASE_URL.split(":")[-1].split("@")[0]
    return settings.PROJECT_DATABASE_TEMPLATE.format(
        project=project_name,
        password=password,
    )


async def get_project_engine(project_name: str):
    """Get or create engine for project database."""
    if project_name not in _project_engines:
        db_url = get_project_database_url(project_name)
        _project_engines[project_name] = create_async_engine(
            db_url,
            echo=settings.DEBUG,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=5,
        )
        _project_session_factories[project_name] = async_sessionmaker(
            _project_engines[project_name],
            class_=AsyncSession,
            expire_on_commit=False,
        )

    return _project_engines[project_name]


async def get_project_session(project_name: str) -> AsyncGenerator[AsyncSession, None]:
    """Get project database session."""
    if project_name not in _project_session_factories:
        await get_project_engine(project_name)

    factory = _project_session_factories[project_name]
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def project_session_context(project_name: str) -> AsyncGenerator[AsyncSession, None]:
    """Context manager for project database session."""
    async for session in get_project_session(project_name):
        yield session


async def create_project_database(project_name: str):
    """Create a new project database with schema."""
    from sqlalchemy import create_engine

    # Use sync engine for DDL operations
    service_url = settings.SERVICE_DATABASE_URL.replace("+asyncpg", "")
    sync_engine = create_engine(service_url)

    db_name = f"{project_name}_vector"

    with sync_engine.connect() as conn:
        # Can't create database in transaction
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        conn.execute(text(f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" TO hostkit_vector'))

    sync_engine.dispose()

    # Connect to new database and create schema
    project_url = get_project_database_url(project_name).replace("+asyncpg", "")
    project_engine = create_engine(project_url)

    with project_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        # Enable pgvector extension
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    # Create tables
    ProjectBase.metadata.create_all(project_engine)

    # Create vector index
    with project_engine.connect() as conn:
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding
            ON chunks USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """))
        conn.commit()

    project_engine.dispose()


async def drop_project_database(project_name: str):
    """Drop a project database."""
    from sqlalchemy import create_engine

    # Remove from cache
    if project_name in _project_engines:
        await _project_engines[project_name].dispose()
        del _project_engines[project_name]
        del _project_session_factories[project_name]

    # Use sync engine for DDL
    service_url = settings.SERVICE_DATABASE_URL.replace("+asyncpg", "")
    sync_engine = create_engine(service_url)

    db_name = f"{project_name}_vector"

    with sync_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        # Terminate existing connections
        conn.execute(text(f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{db_name}'
            AND pid <> pg_backend_pid()
        """))
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))

    sync_engine.dispose()

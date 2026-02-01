"""Test fixtures."""

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from unittest.mock import patch, MagicMock

# Set test environment before importing app
os.environ["SERVICE_DATABASE_URL"] = "postgresql+asyncpg://hostkit_vector:testpass@localhost/hostkit_vector_test"
os.environ["PROJECT_DATABASE_TEMPLATE"] = "postgresql+asyncpg://hostkit_vector:testpass@localhost/{project}_vector_test"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["OPENAI_API_KEY"] = "sk-test-key"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"

from main import app
from models.base import ServiceBase, ProjectBase
from database import get_service_session
from services.auth import generate_api_key
from models.service import VectorProject
from models.project import Collection


# Test database URL (use a separate test database)
TEST_SERVICE_DB_URL = os.environ["SERVICE_DATABASE_URL"]
TEST_PROJECT_DB_URL = os.environ["PROJECT_DATABASE_TEMPLATE"].format(project="testproject")


@pytest_asyncio.fixture
async def service_engine():
    """Create test service database engine."""
    engine = create_async_engine(
        TEST_SERVICE_DB_URL,
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(ServiceBase.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(ServiceBase.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def project_engine():
    """Create test project database engine."""
    engine = create_async_engine(
        TEST_PROJECT_DB_URL,
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(ProjectBase.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(ProjectBase.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def service_session(service_engine):
    """Create test service database session."""
    session_factory = async_sessionmaker(
        service_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def project_session(project_engine):
    """Create test project database session."""
    session_factory = async_sessionmaker(
        project_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_project(service_session):
    """Create a test project with API key."""
    full_key, key_hash, key_prefix = generate_api_key("testproject")

    project = VectorProject(
        project_name="testproject",
        api_key_hash=key_hash,
        api_key_prefix=key_prefix,
    )
    service_session.add(project)
    await service_session.commit()

    return project, full_key


@pytest_asyncio.fixture
async def client(service_session, test_project):
    """Create test HTTP client."""
    project, api_key = test_project

    # Override dependency
    async def override_get_service_session():
        yield service_session

    app.dependency_overrides[get_service_session] = override_get_service_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.headers["Authorization"] = f"Bearer {api_key}"
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def authenticated_client(service_session, project_session, test_project):
    """Create authenticated HTTP client with project database."""
    project, api_key = test_project

    # Mock the project database connection
    async def mock_get_project_session(*args, **kwargs):
        yield project_session

    # Override dependencies
    async def override_get_service_session():
        yield service_session

    app.dependency_overrides[get_service_session] = override_get_service_session

    # We need to patch get_project_db_session in dependencies
    with patch("vector.dependencies.get_project_db_session") as mock_project_db:
        mock_project_db.return_value.__aenter__ = lambda s: project_session
        mock_project_db.return_value.__aexit__ = lambda s, *args: None

        # Use a context manager that returns project_session
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_project_ctx(*args, **kwargs):
            yield project_session

        mock_project_db.side_effect = mock_project_ctx

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.headers["Authorization"] = f"Bearer {api_key}"
            yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_collection(authenticated_client, project_session):
    """Create a test collection."""
    collection_name = "test_collection"

    # Create collection via API
    response = await authenticated_client.post(
        "/collections",
        json={
            "name": collection_name,
            "description": "Test collection for tests",
        }
    )

    # If collection already exists, that's ok
    if response.status_code not in (201, 409):
        response.raise_for_status()

    return collection_name


# Pytest markers
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )

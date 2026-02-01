"""Tests for health endpoint."""

import os
import pytest
from httpx import AsyncClient, ASGITransport

# Set test environment before importing app
os.environ.setdefault("SERVICE_DATABASE_URL", "postgresql+asyncpg://hostkit_vector:testpass@localhost/hostkit_vector_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")

from main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    """Test health endpoint is accessible without auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "services" in data

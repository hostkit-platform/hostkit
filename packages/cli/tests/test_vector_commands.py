"""Tests for vector CLI commands."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from hostkit.cli import cli


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_vector_service():
    """Create a mock vector service."""
    with patch("hostkit.commands.vector.VectorService") as mock_class:
        mock_service = MagicMock()
        mock_class.return_value = mock_service
        yield mock_service


@pytest.fixture
def mock_access():
    """Mock access control decorators."""
    with patch("hostkit.commands.vector.root_only", lambda f: f):
        with patch("hostkit.commands.vector.project_access", lambda arg: lambda f: f):
            yield


class TestVectorStatus:
    """Tests for vector status command."""

    def test_status_success(self, runner, mock_vector_service, mock_access):
        """Test vector status with healthy service."""
        mock_vector_service.status.return_value = {
            "status": "healthy",
            "database": "connected",
            "redis": "connected",
            "worker": "running",
            "project_count": 5,
        }

        result = runner.invoke(cli, ["vector", "status"])

        assert result.exit_code == 0
        assert "healthy" in result.output
        assert "connected" in result.output

    def test_status_json(self, runner, mock_vector_service, mock_access):
        """Test vector status with JSON output."""
        mock_vector_service.status.return_value = {
            "status": "healthy",
            "database": "connected",
            "redis": "connected",
            "worker": "running",
            "project_count": 5,
        }

        result = runner.invoke(cli, ["--json", "vector", "status"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"


class TestVectorEnable:
    """Tests for vector enable command."""

    def test_enable_success(self, runner, mock_vector_service, mock_access):
        """Test enabling vector service for a project."""
        mock_vector_service.enable_project.return_value = {
            "project": "testproject",
            "api_key": "vk_testproject_abc123xyz456",
            "database": "testproject_vector",
            "endpoint": "https://vector.hostkit.dev/v1",
        }

        result = runner.invoke(cli, ["vector", "enable", "testproject"])

        assert result.exit_code == 0
        assert "enabled" in result.output.lower()
        assert "vk_testproject" in result.output
        mock_vector_service.enable_project.assert_called_once_with("testproject")

    def test_enable_json(self, runner, mock_vector_service, mock_access):
        """Test enabling with JSON output."""
        mock_vector_service.enable_project.return_value = {
            "project": "testproject",
            "api_key": "vk_testproject_abc123xyz456",
            "database": "testproject_vector",
            "endpoint": "https://vector.hostkit.dev/v1",
        }

        result = runner.invoke(cli, ["--json", "vector", "enable", "testproject"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["api_key"] == "vk_testproject_abc123xyz456"


class TestVectorCollections:
    """Tests for vector collections commands."""

    def test_list_collections(self, runner, mock_vector_service, mock_access):
        """Test listing collections."""
        mock_vector_service.list_collections.return_value = {
            "collections": [
                {
                    "name": "docs",
                    "document_count": 10,
                    "chunk_count": 150,
                    "created_at": "2024-01-15T00:00:00Z",
                }
            ]
        }

        result = runner.invoke(cli, ["vector", "collections", "testproject"])

        assert result.exit_code == 0
        assert "docs" in result.output

    def test_list_collections_empty(self, runner, mock_vector_service, mock_access):
        """Test listing collections when empty."""
        mock_vector_service.list_collections.return_value = {"collections": []}

        result = runner.invoke(cli, ["vector", "collections", "testproject"])

        assert result.exit_code == 0
        assert "No collections found" in result.output

    def test_create_collection(self, runner, mock_vector_service, mock_access):
        """Test creating a collection."""
        mock_vector_service.create_collection.return_value = {
            "name": "docs",
            "description": "Product documentation",
        }

        result = runner.invoke(
            cli,
            ["vector", "create-collection", "testproject", "docs", "-d", "Product documentation"],
        )

        assert result.exit_code == 0
        assert "created" in result.output.lower()
        mock_vector_service.create_collection.assert_called_once_with(
            "testproject", "docs", "Product documentation"
        )

    def test_delete_collection_with_force(self, runner, mock_vector_service, mock_access):
        """Test deleting a collection with --force."""
        mock_vector_service.delete_collection.return_value = {
            "documents_deleted": 10,
            "chunks_deleted": 150,
        }

        result = runner.invoke(
            cli, ["vector", "delete-collection", "testproject", "docs", "--force"]
        )

        assert result.exit_code == 0
        assert "deleted" in result.output.lower()


class TestVectorSearch:
    """Tests for vector search command."""

    def test_search_success(self, runner, mock_vector_service, mock_access):
        """Test searching a collection."""
        mock_vector_service.search.return_value = {
            "results": [
                {
                    "content": "This is the matched content...",
                    "score": 0.89,
                    "document": {"source_name": "doc.pdf"},
                }
            ],
            "search_time_ms": 45,
        }

        result = runner.invoke(
            cli, ["vector", "search", "testproject", "docs", "test query"]
        )

        assert result.exit_code == 0
        assert "0.89" in result.output
        assert "doc.pdf" in result.output
        mock_vector_service.search.assert_called_once_with(
            "testproject", "docs", "test query", 5, 0.0
        )

    def test_search_no_results(self, runner, mock_vector_service, mock_access):
        """Test search with no results."""
        mock_vector_service.search.return_value = {
            "results": [],
            "search_time_ms": 10,
        }

        result = runner.invoke(
            cli, ["vector", "search", "testproject", "docs", "nonexistent query"]
        )

        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_search_with_options(self, runner, mock_vector_service, mock_access):
        """Test search with custom options."""
        mock_vector_service.search.return_value = {
            "results": [],
            "search_time_ms": 10,
        }

        result = runner.invoke(
            cli,
            [
                "vector", "search", "testproject", "docs", "query",
                "--limit", "10", "--threshold", "0.7",
            ],
        )

        assert result.exit_code == 0
        mock_vector_service.search.assert_called_once_with(
            "testproject", "docs", "query", 10, 0.7
        )

    def test_search_json(self, runner, mock_vector_service, mock_access):
        """Test search with JSON output."""
        mock_vector_service.search.return_value = {
            "results": [
                {
                    "content": "Match",
                    "score": 0.95,
                    "document": {"source_name": "file.txt"},
                }
            ],
            "search_time_ms": 30,
        }

        result = runner.invoke(
            cli, ["--json", "vector", "search", "testproject", "docs", "query"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert len(data["data"]["results"]) == 1


class TestVectorIngest:
    """Tests for vector ingest command."""

    def test_ingest_file(self, runner, mock_vector_service, mock_access):
        """Test ingesting a file."""
        mock_vector_service.ingest_file.return_value = {
            "job_id": "job_abc123",
            "status": "queued",
        }

        result = runner.invoke(
            cli, ["vector", "ingest", "testproject", "docs", "test.pdf"]
        )

        assert result.exit_code == 0
        assert "job_abc123" in result.output
        mock_vector_service.ingest_file.assert_called_once_with(
            "testproject", "docs", "test.pdf"
        )

    def test_ingest_url(self, runner, mock_vector_service, mock_access):
        """Test ingesting a URL."""
        mock_vector_service.ingest_url.return_value = {
            "job_id": "job_xyz789",
            "status": "queued",
        }

        result = runner.invoke(
            cli,
            ["vector", "ingest", "testproject", "docs", "https://example.com", "--url"],
        )

        assert result.exit_code == 0
        assert "job_xyz789" in result.output
        mock_vector_service.ingest_url.assert_called_once_with(
            "testproject", "docs", "https://example.com"
        )

    def test_ingest_stdin(self, runner, mock_vector_service, mock_access):
        """Test ingesting from stdin."""
        mock_vector_service.ingest_text.return_value = {
            "chunks_created": 3,
            "tokens_used": 150,
        }

        result = runner.invoke(
            cli,
            ["vector", "ingest", "testproject", "docs", "-", "--stdin", "--name", "test.txt"],
            input="This is test content",
        )

        assert result.exit_code == 0
        assert "ingested" in result.output.lower()
        mock_vector_service.ingest_text.assert_called_once()


class TestVectorJobs:
    """Tests for vector jobs commands."""

    def test_list_jobs(self, runner, mock_vector_service, mock_access):
        """Test listing jobs."""
        mock_vector_service.list_jobs.return_value = {
            "jobs": [
                {
                    "id": "job_abc123",
                    "collection_name": "docs",
                    "source_identifier": "file.pdf",
                    "status": "completed",
                    "progress": 100,
                }
            ]
        }

        result = runner.invoke(cli, ["vector", "jobs", "testproject"])

        assert result.exit_code == 0
        assert "job_abc123" in result.output
        assert "completed" in result.output

    def test_list_jobs_with_filter(self, runner, mock_vector_service, mock_access):
        """Test listing jobs with status filter."""
        mock_vector_service.list_jobs.return_value = {"jobs": []}

        result = runner.invoke(
            cli, ["vector", "jobs", "testproject", "--status", "failed"]
        )

        assert result.exit_code == 0
        mock_vector_service.list_jobs.assert_called_once_with("testproject", status="failed")

    def test_get_job(self, runner, mock_vector_service, mock_access):
        """Test getting job details."""
        mock_vector_service.get_job.return_value = {
            "id": "job_abc123",
            "collection_name": "docs",
            "source_identifier": "file.pdf",
            "status": "completed",
            "progress": 100,
            "chunks_created": 15,
            "tokens_used": 500,
        }

        result = runner.invoke(cli, ["vector", "job", "testproject", "job_abc123"])

        assert result.exit_code == 0
        assert "job_abc123" in result.output
        assert "completed" in result.output
        assert "15" in result.output


class TestVectorUsage:
    """Tests for vector usage command."""

    def test_usage(self, runner, mock_vector_service, mock_access):
        """Test getting usage statistics."""
        mock_vector_service.get_usage.return_value = {
            "collections": 3,
            "documents": 50,
            "chunks": 750,
            "total_tokens_used": 150000,
            "storage_bytes": 10485760,  # 10 MB
        }

        result = runner.invoke(cli, ["vector", "usage", "testproject"])

        assert result.exit_code == 0
        assert "3" in result.output
        assert "50" in result.output
        assert "750" in result.output
        assert "150,000" in result.output

    def test_usage_json(self, runner, mock_vector_service, mock_access):
        """Test usage with JSON output."""
        mock_vector_service.get_usage.return_value = {
            "collections": 2,
            "documents": 20,
            "chunks": 300,
            "total_tokens_used": 50000,
            "storage_bytes": 5242880,
        }

        result = runner.invoke(cli, ["--json", "vector", "usage", "testproject"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert data["data"]["collections"] == 2


class TestVectorKey:
    """Tests for vector key command."""

    def test_key_info(self, runner, mock_vector_service, mock_access):
        """Test getting key info."""
        mock_vector_service.get_key_info.return_value = {
            "project": "testproject",
            "key_prefix": "vk_testproject_abc12...",
            "created_at": "2024-01-15T00:00:00Z",
            "last_activity_at": "2024-01-16T12:30:00Z",
        }

        result = runner.invoke(cli, ["vector", "key", "testproject"])

        assert result.exit_code == 0
        assert "vk_testproject" in result.output

    def test_key_regenerate(self, runner, mock_vector_service, mock_access):
        """Test regenerating API key."""
        mock_vector_service.regenerate_key.return_value = {
            "project": "testproject",
            "api_key": "vk_testproject_newkey123",
        }

        result = runner.invoke(cli, ["vector", "key", "testproject", "--regenerate"])

        assert result.exit_code == 0
        assert "regenerated" in result.output.lower()
        assert "vk_testproject_newkey123" in result.output


class TestVectorDisable:
    """Tests for vector disable command."""

    def test_disable_with_force(self, runner, mock_vector_service, mock_access):
        """Test disabling vector service with --force."""
        mock_vector_service.disable_project.return_value = {
            "project": "testproject",
            "database_deleted": "testproject_vector",
            "collections_deleted": 3,
            "chunks_deleted": 500,
        }

        result = runner.invoke(
            cli, ["vector", "disable", "testproject", "--force"]
        )

        assert result.exit_code == 0
        assert "disabled" in result.output.lower()
        mock_vector_service.disable_project.assert_called_once_with("testproject")

    def test_disable_without_force_aborts(self, runner, mock_vector_service, mock_access):
        """Test that disable without --force prompts for confirmation."""
        result = runner.invoke(
            cli, ["vector", "disable", "testproject"],
            input="n\n",  # Answer "no" to confirmation
        )

        # Command should abort when user says "no"
        assert result.exit_code != 0 or "Aborted" in result.output
        mock_vector_service.disable_project.assert_not_called()

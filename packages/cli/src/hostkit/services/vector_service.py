"""Vector service management for HostKit.

Provides vector embedding and semantic search services for projects.
"""

import os
import secrets
import string
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import requests
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register vector service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="vector",
        description="Vector embeddings and RAG with pgvector",
        provision_flag=None,
        enable_command="hostkit vector enable {project}",
        env_vars_provided=["VECTOR_API_KEY", "VECTOR_URL"],
        related_commands=["vector enable", "vector create-collection", "vector search"],
    )
)


def _get_pg_connection(database: str = "postgres") -> psycopg2.extensions.connection:
    """Get a PostgreSQL connection using HostKit admin credentials or peer auth."""
    config = get_config()
    pg_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
    pg_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

    # Try password auth first (if password is set)
    if pg_password:
        conn = psycopg2.connect(
            host=config.postgres_host,
            port=config.postgres_port,
            user=pg_user,
            password=pg_password,
            database=database,
        )
    else:
        # Fallback to peer auth (requires running as postgres user)
        conn = psycopg2.connect(
            database=database,
            user="postgres",
        )

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


class VectorServiceError(Exception):
    """Exception raised by vector service operations."""

    def __init__(
        self,
        code: str,
        message: str,
        suggestion: str | None = None,
    ):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


@dataclass
class VectorProjectConfig:
    """Vector service configuration for a project."""

    project: str
    enabled: bool
    api_key_prefix: str | None
    database: str
    created_at: datetime | None
    last_activity_at: datetime | None


class VectorService:
    """Service for managing vector embedding and search functionality."""

    VECTOR_SERVICE_URL = "http://127.0.0.1:8901"
    VECTOR_PUBLIC_URL = "https://vector.hostkit.dev"
    VECTOR_DB = "hostkit_vector"
    VECTOR_SERVICE_DIR = "/var/lib/hostkit/vector"
    VECTOR_LOG_DIR = "/var/log/hostkit/vector"

    def __init__(self):
        self.config = get_config()
        self._session = requests.Session()
        self._session.timeout = 60

    # =========================================================================
    # Service Management (root only)
    # =========================================================================

    def setup(self, force: bool = False) -> dict[str, Any]:
        """Initialize the vector service.

        Creates the service database, deploys the vector service code,
        and starts the systemd services.
        """
        # Check if already setup
        if not force and self._is_setup():
            raise VectorServiceError(
                code="ALREADY_SETUP",
                message="Vector service is already set up",
                suggestion="Use --force to reinitialize",
            )

        # Create log directory
        log_dir = Path(self.VECTOR_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create service database
        self._create_service_database()

        # Deploy service files (templates are synced via deploy.sh)
        self._deploy_service()

        # Start services
        self._start_services()

        return {
            "service_url": self.VECTOR_PUBLIC_URL,
            "database": self.VECTOR_DB,
            "service_dir": self.VECTOR_SERVICE_DIR,
            "log_dir": self.VECTOR_LOG_DIR,
        }

    def status(self) -> dict[str, Any]:
        """Get vector service status."""
        result = {
            "status": "unknown",
            "database": "unknown",
            "redis": "unknown",
            "worker": "unknown",
            "project_count": 0,
        }

        # Check service health
        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/health",
                timeout=5,
            )
            if response.status_code == 200:
                health_data = response.json()
                result["status"] = health_data.get("status", "healthy")
                result["database"] = health_data.get("database", "connected")
                result["redis"] = health_data.get("redis", "connected")
            else:
                result["status"] = "unhealthy"
        except requests.RequestException:
            result["status"] = "offline"
            result["database"] = "unknown"
            result["redis"] = "unknown"

        # Check worker status via systemctl
        try:
            worker_result = subprocess.run(
                ["systemctl", "is-active", "hostkit-vector-worker"],
                capture_output=True,
                text=True,
            )
            result["worker"] = worker_result.stdout.strip() or "inactive"
        except Exception:
            result["worker"] = "unknown"

        # Count enabled projects
        try:
            db = get_db()
            with db.connection() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM vector_projects WHERE enabled = 1"
                ).fetchone()[0]
                result["project_count"] = count
        except Exception:
            pass

        return result

    def _is_setup(self) -> bool:
        """Check if vector service is already set up."""
        # Check if database exists
        try:
            conn = _get_pg_connection(self.VECTOR_DB)
            conn.close()
            return True
        except psycopg2.OperationalError:
            return False

    def _create_service_database(self) -> None:
        """Create the vector service database."""
        try:
            conn = _get_pg_connection("postgres")
            cursor = conn.cursor()

            # Check if database exists
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (self.VECTOR_DB,),
            )
            if not cursor.fetchone():
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.VECTOR_DB)))

            cursor.close()
            conn.close()

            # Connect to new database and enable pgvector
            conn = _get_pg_connection(self.VECTOR_DB)
            cursor = conn.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.close()
            conn.close()

        except psycopg2.Error as e:
            raise VectorServiceError(
                code="DATABASE_ERROR",
                message=f"Failed to create database: {e}",
                suggestion="Check PostgreSQL is running and accessible",
            )

    def _deploy_service(self) -> None:
        """Deploy the vector service files."""
        service_dir = Path(self.VECTOR_SERVICE_DIR)

        # Service files should already be in templates/vector from deploy.sh sync
        templates_dir = Path("/var/lib/hostkit/templates/vector")

        if not templates_dir.exists():
            raise VectorServiceError(
                code="TEMPLATES_NOT_FOUND",
                message="Vector service templates not found",
                suggestion="Run deploy.sh to sync templates to VPS",
            )

        # Copy files to service directory
        service_dir.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["rsync", "-av", "--delete", f"{templates_dir}/", f"{service_dir}/"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise VectorServiceError(
                code="DEPLOY_ERROR",
                message=f"Failed to deploy service files: {result.stderr}",
            )

        # Create virtual environment and install dependencies
        venv_dir = service_dir / "venv"
        if not venv_dir.exists():
            subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                check=True,
            )

        # Install dependencies
        pip_path = venv_dir / "bin" / "pip"
        requirements_path = service_dir / "requirements.txt"

        result = subprocess.run(
            [str(pip_path), "install", "-r", str(requirements_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise VectorServiceError(
                code="INSTALL_ERROR",
                message=f"Failed to install dependencies: {result.stderr}",
            )

        # Copy systemd service files
        systemd_dir = service_dir / "systemd"
        for service_file in systemd_dir.glob("*.service"):
            subprocess.run(
                ["cp", str(service_file), "/etc/systemd/system/"],
                check=True,
            )

        # Reload systemd
        subprocess.run(["systemctl", "daemon-reload"], check=True)

    def _start_services(self) -> None:
        """Start the vector systemd services."""
        services = ["hostkit-vector", "hostkit-vector-worker"]

        for service in services:
            subprocess.run(["systemctl", "enable", service], check=True)
            subprocess.run(["systemctl", "start", service], check=True)

    # =========================================================================
    # Project Management
    # =========================================================================

    def enable_project(self, project: str) -> dict[str, Any]:
        """Enable vector service for a project.

        Creates a project-specific database and generates an API key.
        """
        # Verify project exists
        db = get_db()
        with db.connection() as conn:
            proj = conn.execute("SELECT name FROM projects WHERE name = ?", (project,)).fetchone()
            if not proj:
                raise VectorServiceError(
                    code="PROJECT_NOT_FOUND",
                    message=f"Project '{project}' does not exist",
                    suggestion="Create the project first with 'hostkit project create'",
                )

            # Check if already enabled
            existing = conn.execute(
                "SELECT id FROM vector_projects WHERE project_name = ?", (project,)
            ).fetchone()
            if existing:
                raise VectorServiceError(
                    code="ALREADY_ENABLED",
                    message=f"Vector service already enabled for '{project}'",
                    suggestion="Use 'hostkit vector key --regenerate' to get a new API key",
                )

        # Generate API key
        api_key = self._generate_api_key(project)
        key_hash = self._hash_api_key(api_key)
        key_prefix = api_key[:20]

        # Create project database
        project_db = f"{project}_vector"
        self._create_project_database(project_db)

        # Register project in vector service database
        try:
            conn = _get_pg_connection(self.VECTOR_DB)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO vector_projects
                (project_name, api_key_hash, api_key_prefix, is_active, settings)
                VALUES (%s, %s, %s, true, %s)
                """,
                (project, key_hash, key_prefix, "{}"),
            )
            cursor.close()
            conn.close()
        except psycopg2.Error as e:
            # Cleanup database on failure
            self._drop_project_database(project_db)
            raise VectorServiceError(
                code="DATABASE_ERROR",
                message=f"Failed to register project: {e}",
            )

        # Store in hostkit database (including API key for CLI access)
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO vector_projects
                (project_name, enabled, api_key, api_key_hash,
                api_key_prefix, database_name, created_at)
                VALUES (?, 1, ?, ?, ?, ?, datetime('now'))
                """,
                (project, api_key, key_hash, key_prefix, project_db),
            )

        return {
            "project": project,
            "api_key": api_key,
            "database": project_db,
            "endpoint": f"{self.VECTOR_PUBLIC_URL}/v1",
        }

    def disable_project(self, project: str) -> dict[str, Any]:
        """Disable vector service for a project.

        Removes the project database and all data.
        """
        db = get_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT database_name FROM vector_projects WHERE project_name = ?",
                (project,),
            ).fetchone()

            if not row:
                raise VectorServiceError(
                    code="NOT_ENABLED",
                    message=f"Vector service not enabled for '{project}'",
                )

            project_db = row[0]

        # Get counts before deletion
        try:
            conn = _get_pg_connection(project_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM collections")
            collections_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM chunks")
            chunks_count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
        except Exception:
            collections_count = 0
            chunks_count = 0

        # Delete from vector service database
        try:
            conn = _get_pg_connection(self.VECTOR_DB)
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM vector_projects WHERE project_name = %s",
                (project,),
            )
            cursor.close()
            conn.close()
        except psycopg2.Error:
            pass  # Best effort cleanup

        # Drop project database
        self._drop_project_database(project_db)

        # Remove from hostkit database
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM vector_projects WHERE project_name = ?",
                (project,),
            )

        return {
            "project": project,
            "database_deleted": project_db,
            "collections_deleted": collections_count,
            "chunks_deleted": chunks_count,
        }

    def regenerate_key(self, project: str) -> dict[str, Any]:
        """Regenerate API key for a project."""
        db = get_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id FROM vector_projects WHERE project_name = ?",
                (project,),
            ).fetchone()

            if not row:
                raise VectorServiceError(
                    code="NOT_ENABLED",
                    message=f"Vector service not enabled for '{project}'",
                    suggestion=f"Enable first with 'hostkit vector enable {project}'",
                )

        # Generate new key
        api_key = self._generate_api_key(project)
        key_hash = self._hash_api_key(api_key)
        key_prefix = api_key[:20]

        # Update vector service database
        try:
            conn = _get_pg_connection(self.VECTOR_DB)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE vector_projects
                SET api_key_hash = %s, api_key_prefix = %s, updated_at = now()
                WHERE project_name = %s
                """,
                (key_hash, key_prefix, project),
            )
            cursor.close()
            conn.close()
        except psycopg2.Error as e:
            raise VectorServiceError(
                code="DATABASE_ERROR",
                message=f"Failed to update key: {e}",
            )

        # Update hostkit database
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE vector_projects
                SET api_key = ?, api_key_hash = ?, api_key_prefix = ?
                WHERE project_name = ?
                """,
                (api_key, key_hash, key_prefix, project),
            )

        return {
            "project": project,
            "api_key": api_key,
        }

    def get_key_info(self, project: str) -> dict[str, Any]:
        """Get API key info for a project (without revealing the full key)."""
        db = get_db()
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT api_key_prefix, created_at, last_activity_at
                FROM vector_projects
                WHERE project_name = ?
                """,
                (project,),
            ).fetchone()

            if not row:
                raise VectorServiceError(
                    code="NOT_ENABLED",
                    message=f"Vector service not enabled for '{project}'",
                )

            return {
                "project": project,
                "key_prefix": row[0] + "...",
                "created_at": row[1],
                "last_activity_at": row[2],
            }

    # =========================================================================
    # Collections
    # =========================================================================

    def list_collections(self, project: str) -> dict[str, Any]:
        """List collections for a project."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/v1/collections",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                return {"collections": data.get("data", {}).get("collections", [])}
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to list collections: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def create_collection(
        self,
        project: str,
        name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a collection."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.post(
                f"{self.VECTOR_SERVICE_URL}/v1/collections",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "name": name,
                    "description": description,
                },
                timeout=30,
            )

            if response.status_code in (200, 201):
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to create collection: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def delete_collection(self, project: str, name: str) -> dict[str, Any]:
        """Delete a collection."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.delete(
                f"{self.VECTOR_SERVICE_URL}/v1/collections/{name}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to delete collection: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def get_collection_info(self, project: str, collection: str) -> dict[str, Any]:
        """Get collection details."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/v1/collections/{collection}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to get collection: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    # =========================================================================
    # Document Ingestion
    # =========================================================================

    def ingest_text(
        self,
        project: str,
        collection: str,
        content: str,
        source_name: str = "stdin",
    ) -> dict[str, Any]:
        """Ingest text content (sync)."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.post(
                f"{self.VECTOR_SERVICE_URL}/v1/collections/{collection}/documents",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "content": content,
                    "source_type": "text",
                    "source_name": source_name,
                },
                timeout=120,
            )

            if response.status_code in (200, 201, 202):
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to ingest text: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def ingest_url(self, project: str, collection: str, url: str) -> dict[str, Any]:
        """Ingest content from URL (async)."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.post(
                f"{self.VECTOR_SERVICE_URL}/v1/collections/{collection}/documents",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "url": url,
                },
                timeout=30,
            )

            if response.status_code in (200, 201, 202):
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to queue URL ingestion: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def ingest_file(
        self,
        project: str,
        collection: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Ingest a file (async)."""
        api_key = self._get_api_key_for_project(project)
        path = Path(file_path)

        if not path.exists():
            raise VectorServiceError(
                code="FILE_NOT_FOUND",
                message=f"File not found: {file_path}",
            )

        try:
            with open(path, "rb") as f:
                response = self._session.post(
                    f"{self.VECTOR_SERVICE_URL}/v1/collections/{collection}/documents",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (path.name, f)},
                    timeout=60,
                )

            if response.status_code in (200, 201, 202):
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to queue file ingestion: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    # =========================================================================
    # Search
    # =========================================================================

    def search(
        self,
        project: str,
        collection: str,
        query: str,
        limit: int = 5,
        threshold: float = 0.0,
    ) -> dict[str, Any]:
        """Search a collection."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.post(
                f"{self.VECTOR_SERVICE_URL}/v1/collections/{collection}/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "query": query,
                    "limit": limit,
                    "threshold": threshold,
                },
                timeout=30,
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Search failed: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    # =========================================================================
    # Jobs
    # =========================================================================

    def list_jobs(
        self,
        project: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List jobs for a project."""
        api_key = self._get_api_key_for_project(project)

        params = {}
        if status:
            params["status"] = status

        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/v1/jobs",
                headers={"Authorization": f"Bearer {api_key}"},
                params=params,
                timeout=30,
            )

            if response.status_code == 200:
                return {"jobs": response.json().get("data", {}).get("jobs", [])}
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to list jobs: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def get_job(self, project: str, job_id: str) -> dict[str, Any]:
        """Get job details."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to get job: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    def wait_for_job(
        self,
        project: str,
        job_id: str,
        timeout: int = 300,
        poll_interval: int = 2,
    ) -> dict[str, Any]:
        """Wait for a job to complete."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            job = self.get_job(project, job_id)
            status = job.get("status")

            if status in ("completed", "failed", "cancelled"):
                return job

            time.sleep(poll_interval)

        raise VectorServiceError(
            code="TIMEOUT",
            message=f"Job {job_id} did not complete within {timeout} seconds",
        )

    # =========================================================================
    # Usage Statistics
    # =========================================================================

    def get_usage(self, project: str) -> dict[str, Any]:
        """Get usage statistics for a project."""
        api_key = self._get_api_key_for_project(project)

        try:
            response = self._session.get(
                f"{self.VECTOR_SERVICE_URL}/v1/usage",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                raise VectorServiceError(
                    code="API_ERROR",
                    message=f"Failed to get usage: {response.text}",
                )

        except requests.RequestException as e:
            raise VectorServiceError(
                code="SERVICE_UNAVAILABLE",
                message=f"Vector service unavailable: {e}",
            )

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _generate_api_key(self, project: str) -> str:
        """Generate a new API key for a project."""
        random_part = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(32)
        )
        return f"vk_{project}_{random_part}"

    def _hash_api_key(self, api_key: str) -> str:
        """Hash an API key for storage."""
        import hashlib

        return hashlib.sha256(api_key.encode()).hexdigest()

    def _get_api_key_for_project(self, project: str) -> str:
        """Get the API key for a project from the database."""
        db = get_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT api_key FROM vector_projects WHERE project_name = ?",
                (project,),
            ).fetchone()

            if not row:
                raise VectorServiceError(
                    code="NOT_ENABLED",
                    message=f"Vector service not enabled for '{project}'",
                    suggestion=f"Enable first with 'hostkit vector enable {project}'",
                )

            if not row[0]:
                raise VectorServiceError(
                    code="NO_API_KEY",
                    message=f"No API key stored for '{project}'",
                    suggestion=f"Regenerate key with 'hostkit vector key {project} --regenerate'",
                )

            return row[0]

    def _create_project_database(self, db_name: str) -> None:
        """Create a project-specific vector database with schema."""
        try:
            conn = _get_pg_connection("postgres")
            cursor = conn.cursor()

            # Create database
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

            # Grant privileges to vector user
            cursor.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO hostkit_vector").format(
                    sql.Identifier(db_name)
                )
            )

            cursor.close()
            conn.close()

            # Enable pgvector and create schema in new database
            conn = _get_pg_connection(db_name)
            cursor = conn.cursor()

            # Enable pgvector extension
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # Create collections table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT,
                    document_count INTEGER DEFAULT 0,
                    chunk_count INTEGER DEFAULT 0,
                    extra_data JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)

            # Create documents table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    source_type VARCHAR(20) NOT NULL,
                    source_name VARCHAR(1024) NOT NULL,
                    source_url TEXT,
                    content_hash VARCHAR(64),
                    chunk_count INTEGER DEFAULT 0,
                    token_count INTEGER DEFAULT 0,
                    extra_data JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_source_name ON documents(source_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)"
            )

            # Create chunks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id SERIAL PRIMARY KEY,
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    embedding vector(1536),
                    chunk_index INTEGER NOT NULL,
                    token_count INTEGER,
                    extra_data JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_collection ON chunks(collection_id)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS"
                " idx_chunks_collection_index"
                " ON chunks(collection_id, chunk_index)"
            )

            # Create vector index (IVFFlat)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                ON chunks USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)

            # Grant permissions to hostkit_vector user
            cursor.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO hostkit_vector")
            cursor.execute(
                "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO hostkit_vector"
            )

            cursor.close()
            conn.close()

        except psycopg2.Error as e:
            raise VectorServiceError(
                code="DATABASE_ERROR",
                message=f"Failed to create project database: {e}",
            )

    def _drop_project_database(self, db_name: str) -> None:
        """Drop a project-specific vector database."""
        try:
            conn = _get_pg_connection("postgres")
            cursor = conn.cursor()

            # Terminate existing connections
            cursor.execute(
                sql.SQL("""
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.datname = %s
                    AND pid <> pg_backend_pid()
                """),
                (db_name,),
            )

            # Drop database
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))

            cursor.close()
            conn.close()

        except psycopg2.Error:
            # Ignore errors when dropping - cleanup best effort
            pass

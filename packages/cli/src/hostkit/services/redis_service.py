"""Redis management service for HostKit."""

from dataclasses import dataclass
from typing import Any

import redis

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register service with capabilities registry
CapabilitiesRegistry.register_service(ServiceMeta(
    name="redis",
    description="Redis cache/queue (auto-assigned db number)",
    provision_flag=None,
    enable_command=None,
    env_vars_provided=["REDIS_URL"],
    related_commands=["redis info", "redis flush"],
))


@dataclass
class RedisInfo:
    """Redis server information."""

    version: str
    uptime_seconds: int
    connected_clients: int
    used_memory: str
    used_memory_peak: str
    total_keys: int
    databases: dict[int, int]  # db_number -> key_count


class RedisServiceError(Exception):
    """Base exception for Redis service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class RedisService:
    """Service for managing Redis for HostKit projects."""

    def __init__(self) -> None:
        self.config = get_config()
        self.hostkit_db = get_db()

    def _get_connection(self, db: int = 0) -> redis.Redis:
        """Get a Redis connection."""
        try:
            client = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                db=db,
                decode_responses=True,
            )
            # Test connection
            client.ping()
            return client
        except redis.ConnectionError as e:
            raise RedisServiceError(
                code="REDIS_CONNECTION_FAILED",
                message=f"Failed to connect to Redis: {e}",
                suggestion="Check that Redis is running with 'systemctl status redis'",
            )
        except redis.RedisError as e:
            raise RedisServiceError(
                code="REDIS_ERROR",
                message=f"Redis error: {e}",
                suggestion="Check Redis configuration and logs",
            )

    def get_info(self) -> RedisInfo:
        """Get Redis server information."""
        client = self._get_connection()
        try:
            info = client.info()

            # Count keys per database
            databases: dict[int, int] = {}
            for key, value in info.items():
                if key.startswith("db") and key[2:].isdigit():
                    db_num = int(key[2:])
                    databases[db_num] = value.get("keys", 0)

            total_keys = sum(databases.values())

            return RedisInfo(
                version=info.get("redis_version", "unknown"),
                uptime_seconds=info.get("uptime_in_seconds", 0),
                connected_clients=info.get("connected_clients", 0),
                used_memory=info.get("used_memory_human", "0B"),
                used_memory_peak=info.get("used_memory_peak_human", "0B"),
                total_keys=total_keys,
                databases=databases,
            )
        finally:
            client.close()

    def get_keys(
        self, project_name: str, pattern: str = "*", limit: int = 100
    ) -> dict[str, Any]:
        """Get keys for a project's Redis database."""
        # Get project's Redis database number
        project = self.hostkit_db.get_project(project_name)
        if not project:
            raise RedisServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        redis_db = project.get("redis_db")
        if redis_db is None:
            raise RedisServiceError(
                code="NO_REDIS_DB",
                message=f"Project '{project_name}' has no Redis database assigned",
                suggestion="Redis database is assigned when project is created",
            )

        client = self._get_connection(db=redis_db)
        try:
            # Get matching keys (with limit)
            keys = []
            cursor = 0
            while len(keys) < limit:
                cursor, batch = client.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break

            # Trim to limit
            keys = keys[:limit]

            # Get key count for this database
            db_size = client.dbsize()

            return {
                "project": project_name,
                "redis_db": redis_db,
                "pattern": pattern,
                "keys": keys,
                "count": len(keys),
                "total_in_db": db_size,
                "limited": len(keys) >= limit,
            }
        finally:
            client.close()

    def flush_db(self, project_name: str, force: bool = False) -> dict[str, Any]:
        """Flush all keys in a project's Redis database."""
        if not force:
            raise RedisServiceError(
                code="FORCE_REQUIRED",
                message="Flushing Redis database requires --force flag",
                suggestion="Add --force to confirm flushing all keys",
            )

        # Get project's Redis database number
        project = self.hostkit_db.get_project(project_name)
        if not project:
            raise RedisServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        redis_db = project.get("redis_db")
        if redis_db is None:
            raise RedisServiceError(
                code="NO_REDIS_DB",
                message=f"Project '{project_name}' has no Redis database assigned",
                suggestion="Redis database is assigned when project is created",
            )

        client = self._get_connection(db=redis_db)
        try:
            # Get key count before flush
            keys_before = client.dbsize()

            # Flush the database
            client.flushdb()

            return {
                "project": project_name,
                "redis_db": redis_db,
                "keys_deleted": keys_before,
            }
        finally:
            client.close()

    def get_project_redis_url(self, project_name: str) -> str:
        """Get the Redis URL for a project."""
        project = self.hostkit_db.get_project(project_name)
        if not project:
            raise RedisServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        redis_db = project.get("redis_db", 0)
        return f"redis://{self.config.redis_host}:{self.config.redis_port}/{redis_db}"

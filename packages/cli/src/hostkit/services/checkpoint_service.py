"""Database checkpoint service for HostKit.

Provides point-in-time database snapshots using pg_dump for safe rollbacks
during migrations, deployments, and other risky operations.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hostkit.config import get_config
from hostkit.database import get_db


@dataclass
class CheckpointInfo:
    """Information about a database checkpoint."""

    id: int
    project_name: str
    label: str | None
    checkpoint_type: str
    trigger_source: str | None
    database_name: str
    backup_path: str
    size_bytes: int
    created_at: str
    created_by: str
    expires_at: str | None


class CheckpointServiceError(Exception):
    """Base exception for checkpoint service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Retention policy in days (None = never auto-delete)
RETENTION_POLICY = {
    "manual": None,
    "pre_migration": 30,
    "pre_deploy": 14,
    "pre_restore": 7,
    "auto": 7,
}


class CheckpointService:
    """Service for managing database checkpoints."""

    def __init__(self) -> None:
        self.config = get_config()
        self.hostkit_db = get_db()
        self._admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        self._admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

    def _db_name(self, project_name: str) -> str:
        """Generate database name for a project."""
        return f"{project_name}_db"

    def _checkpoint_dir(self, project_name: str) -> Path:
        """Get checkpoint directory for a project."""
        return self.config.backup_dir / project_name / "checkpoints"

    def _get_current_user(self) -> str:
        """Get the current user (for audit trail)."""
        return os.environ.get("SUDO_USER") or os.environ.get("USER", "system")

    def _database_exists(self, project_name: str) -> bool:
        """Check if a database exists for a project."""
        from hostkit.services.database_service import DatabaseService

        db_service = DatabaseService()
        return db_service.database_exists(project_name)

    def _calculate_expiry(self, checkpoint_type: str) -> str | None:
        """Calculate expiry date based on checkpoint type."""
        retention_days = RETENTION_POLICY.get(checkpoint_type)
        if retention_days is None:
            return None
        expiry = datetime.utcnow() + timedelta(days=retention_days)
        return expiry.isoformat()

    def create_checkpoint(
        self,
        project_name: str,
        label: str | None = None,
        checkpoint_type: str = "manual",
        trigger_source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointInfo:
        """Create a database checkpoint using pg_dump.

        Args:
            project_name: Name of the project
            label: Optional human-readable label for the checkpoint
            checkpoint_type: Type of checkpoint (manual, pre_migration, pre_restore, auto)
            trigger_source: What triggered this checkpoint (migrate, restore, user, claude)
            metadata: Optional additional metadata to store

        Returns:
            CheckpointInfo with checkpoint details
        """
        # Validate project exists
        project = self.hostkit_db.get_project(project_name)
        if not project:
            raise CheckpointServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        # Validate database exists
        db_name = self._db_name(project_name)
        if not self._database_exists(project_name):
            raise CheckpointServiceError(
                code="DATABASE_NOT_FOUND",
                message=f"Database '{db_name}' does not exist",
                suggestion="Create the database first with 'hostkit db create'",
            )

        # Create checkpoint directory
        checkpoint_dir = self._checkpoint_dir(project_name)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Generate checkpoint filename with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        checkpoint_filename = f"checkpoint_{timestamp}.sql.gz"
        checkpoint_path = checkpoint_dir / checkpoint_filename

        # Build pg_dump command
        env = os.environ.copy()
        if self._admin_password:
            env["PGPASSWORD"] = self._admin_password

        pg_dump_cmd = [
            "pg_dump",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self._admin_user,
            "-d", db_name,
            "--no-owner",
            "--no-acl",
        ]

        try:
            # Run pg_dump and pipe to gzip
            with open(checkpoint_path, "wb") as f:
                pg_dump_proc = subprocess.Popen(
                    pg_dump_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )

                gzip_proc = subprocess.Popen(
                    ["gzip", "-c"],
                    stdin=pg_dump_proc.stdout,
                    stdout=f,
                    stderr=subprocess.PIPE,
                )

                pg_dump_proc.stdout.close()  # Allow pg_dump to receive SIGPIPE
                _, gzip_stderr = gzip_proc.communicate()
                _, pg_dump_stderr = pg_dump_proc.communicate()

                if pg_dump_proc.returncode != 0:
                    # Clean up failed checkpoint file
                    checkpoint_path.unlink(missing_ok=True)
                    raise CheckpointServiceError(
                        code="CHECKPOINT_FAILED",
                        message=f"pg_dump failed: {pg_dump_stderr.decode()}",
                        suggestion="Check database exists and credentials are correct",
                    )

                if gzip_proc.returncode != 0:
                    checkpoint_path.unlink(missing_ok=True)
                    raise CheckpointServiceError(
                        code="CHECKPOINT_FAILED",
                        message=f"gzip failed: {gzip_stderr.decode()}",
                        suggestion="Check disk space and permissions",
                    )

        except FileNotFoundError:
            raise CheckpointServiceError(
                code="COMMAND_NOT_FOUND",
                message="pg_dump or gzip not found",
                suggestion="Ensure PostgreSQL client tools are installed",
            )

        # Get checkpoint file size
        checkpoint_size = checkpoint_path.stat().st_size

        # Calculate expiry
        expires_at = self._calculate_expiry(checkpoint_type)

        # Record checkpoint in database
        checkpoint_record = self.hostkit_db.create_checkpoint(
            project_name=project_name,
            checkpoint_type=checkpoint_type,
            database_name=db_name,
            backup_path=str(checkpoint_path),
            size_bytes=checkpoint_size,
            created_by=self._get_current_user(),
            label=label,
            trigger_source=trigger_source,
            expires_at=expires_at,
            metadata=json.dumps(metadata) if metadata else None,
        )

        return CheckpointInfo(
            id=checkpoint_record["id"],
            project_name=checkpoint_record["project_name"],
            label=checkpoint_record["label"],
            checkpoint_type=checkpoint_record["checkpoint_type"],
            trigger_source=checkpoint_record["trigger_source"],
            database_name=checkpoint_record["database_name"],
            backup_path=checkpoint_record["backup_path"],
            size_bytes=checkpoint_record["size_bytes"],
            created_at=checkpoint_record["created_at"],
            created_by=checkpoint_record["created_by"],
            expires_at=checkpoint_record["expires_at"],
        )

    def list_checkpoints(
        self,
        project_name: str,
        checkpoint_type: str | None = None,
        limit: int = 20,
    ) -> list[CheckpointInfo]:
        """List checkpoints for a project.

        Args:
            project_name: Name of the project
            checkpoint_type: Optional filter by type
            limit: Maximum number of checkpoints to return

        Returns:
            List of CheckpointInfo
        """
        # Validate project exists
        project = self.hostkit_db.get_project(project_name)
        if not project:
            raise CheckpointServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project_name}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

        checkpoints = self.hostkit_db.list_checkpoints(
            project_name=project_name,
            checkpoint_type=checkpoint_type,
            limit=limit,
        )

        return [
            CheckpointInfo(
                id=cp["id"],
                project_name=cp["project_name"],
                label=cp["label"],
                checkpoint_type=cp["checkpoint_type"],
                trigger_source=cp["trigger_source"],
                database_name=cp["database_name"],
                backup_path=cp["backup_path"],
                size_bytes=cp["size_bytes"],
                created_at=cp["created_at"],
                created_by=cp["created_by"],
                expires_at=cp["expires_at"],
            )
            for cp in checkpoints
        ]

    def get_checkpoint(self, checkpoint_id: int) -> CheckpointInfo:
        """Get a checkpoint by ID.

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            CheckpointInfo
        """
        cp = self.hostkit_db.get_checkpoint(checkpoint_id)
        if not cp:
            raise CheckpointServiceError(
                code="CHECKPOINT_NOT_FOUND",
                message=f"Checkpoint {checkpoint_id} not found",
                suggestion="Run 'hostkit checkpoint list <project>' to see available checkpoints",
            )

        return CheckpointInfo(
            id=cp["id"],
            project_name=cp["project_name"],
            label=cp["label"],
            checkpoint_type=cp["checkpoint_type"],
            trigger_source=cp["trigger_source"],
            database_name=cp["database_name"],
            backup_path=cp["backup_path"],
            size_bytes=cp["size_bytes"],
            created_at=cp["created_at"],
            created_by=cp["created_by"],
            expires_at=cp["expires_at"],
        )

    def restore_checkpoint(
        self,
        project_name: str,
        checkpoint_id: int,
        create_pre_restore: bool = True,
    ) -> dict[str, Any]:
        """Restore a database from a checkpoint.

        Args:
            project_name: Name of the project
            checkpoint_id: ID of the checkpoint to restore
            create_pre_restore: Whether to create a checkpoint before restoring

        Returns:
            Dict with restore details
        """
        # Get checkpoint
        checkpoint = self.get_checkpoint(checkpoint_id)

        # Verify checkpoint belongs to this project
        if checkpoint.project_name != project_name:
            raise CheckpointServiceError(
                code="CHECKPOINT_MISMATCH",
                message=f"Checkpoint {checkpoint_id} belongs to project '{checkpoint.project_name}', not '{project_name}'",
                suggestion="Specify the correct project or checkpoint ID",
            )

        # Verify backup file exists
        backup_path = Path(checkpoint.backup_path)
        if not backup_path.exists():
            raise CheckpointServiceError(
                code="BACKUP_FILE_MISSING",
                message=f"Checkpoint file not found: {checkpoint.backup_path}",
                suggestion="The checkpoint file may have been deleted",
            )

        # Create a safety checkpoint before restore
        pre_restore_checkpoint = None
        if create_pre_restore:
            pre_restore_checkpoint = self.create_checkpoint(
                project_name=project_name,
                label=f"pre-restore-{checkpoint_id}",
                checkpoint_type="pre_restore",
                trigger_source="restore",
                metadata={"restoring_from": checkpoint_id},
            )

        # Get database name
        db_name = checkpoint.database_name

        # Build environment
        env = os.environ.copy()
        if self._admin_password:
            env["PGPASSWORD"] = self._admin_password

        # Terminate existing connections
        try:
            import psycopg2
            from psycopg2 import sql
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

            conn = psycopg2.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                user=self._admin_user,
                password=self._admin_password,
                database="postgres",
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

            try:
                with conn.cursor() as cur:
                    # Terminate active connections
                    cur.execute(
                        """
                        SELECT pg_terminate_backend(pg_stat_activity.pid)
                        FROM pg_stat_activity
                        WHERE pg_stat_activity.datname = %s
                        AND pid <> pg_backend_pid()
                        """,
                        [db_name],
                    )

                    # Drop and recreate database
                    role_name = f"{project_name}_user"
                    cur.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {}").format(
                            sql.Identifier(db_name)
                        )
                    )
                    cur.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(db_name), sql.Identifier(role_name)
                        )
                    )
            finally:
                conn.close()

        except Exception as e:
            raise CheckpointServiceError(
                code="RESTORE_PREP_FAILED",
                message=f"Failed to prepare database for restore: {e}",
                suggestion="Check PostgreSQL connection and permissions",
            )

        # Build psql restore command
        psql_cmd = [
            "psql",
            "-h", self.config.postgres_host,
            "-p", str(self.config.postgres_port),
            "-U", self._admin_user,
            "-d", db_name,
            "-q",  # Quiet mode
        ]

        try:
            # Decompress and pipe to psql
            gunzip_proc = subprocess.Popen(
                ["gunzip", "-c", str(backup_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            psql_proc = subprocess.Popen(
                psql_cmd,
                stdin=gunzip_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            gunzip_proc.stdout.close()
            _, psql_stderr = psql_proc.communicate()
            _, gunzip_stderr = gunzip_proc.communicate()

            if gunzip_proc.returncode != 0:
                raise CheckpointServiceError(
                    code="RESTORE_FAILED",
                    message=f"gunzip failed: {gunzip_stderr.decode()}",
                    suggestion="Check checkpoint file is valid gzip",
                )

            if psql_proc.returncode != 0:
                raise CheckpointServiceError(
                    code="RESTORE_FAILED",
                    message=f"psql restore failed: {psql_stderr.decode()}",
                    suggestion="Check database credentials and checkpoint file format",
                )

        except FileNotFoundError as e:
            raise CheckpointServiceError(
                code="COMMAND_NOT_FOUND",
                message=f"Required command not found: {e}",
                suggestion="Ensure PostgreSQL client tools are installed",
            )

        return {
            "project": project_name,
            "database": db_name,
            "restored_from_checkpoint": checkpoint_id,
            "checkpoint_label": checkpoint.label,
            "checkpoint_created_at": checkpoint.created_at,
            "pre_restore_checkpoint_id": pre_restore_checkpoint.id if pre_restore_checkpoint else None,
            "restored_at": datetime.utcnow().isoformat(),
        }

    def delete_checkpoint(
        self,
        project_name: str,
        checkpoint_id: int,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a checkpoint.

        Args:
            project_name: Name of the project
            checkpoint_id: ID of the checkpoint to delete
            force: Required for deletion

        Returns:
            Dict with deletion details
        """
        if not force:
            raise CheckpointServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a checkpoint requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        # Get checkpoint
        checkpoint = self.get_checkpoint(checkpoint_id)

        # Verify checkpoint belongs to this project
        if checkpoint.project_name != project_name:
            raise CheckpointServiceError(
                code="CHECKPOINT_MISMATCH",
                message=f"Checkpoint {checkpoint_id} belongs to project '{checkpoint.project_name}', not '{project_name}'",
                suggestion="Specify the correct project or checkpoint ID",
            )

        # Delete backup file
        backup_path = Path(checkpoint.backup_path)
        if backup_path.exists():
            backup_path.unlink()

        # Delete database record
        self.hostkit_db.delete_checkpoint(checkpoint_id)

        return {
            "deleted_checkpoint_id": checkpoint_id,
            "project": project_name,
            "label": checkpoint.label,
            "size_bytes": checkpoint.size_bytes,
            "deleted_at": datetime.utcnow().isoformat(),
        }

    def cleanup_expired_checkpoints(self) -> dict[str, Any]:
        """Remove expired checkpoints.

        Returns:
            Dict with cleanup summary
        """
        expired = self.hostkit_db.get_expired_checkpoints()
        deleted_count = 0
        freed_bytes = 0
        errors = []

        for cp in expired:
            try:
                # Delete backup file
                backup_path = Path(cp["backup_path"])
                if backup_path.exists():
                    freed_bytes += backup_path.stat().st_size
                    backup_path.unlink()

                # Delete database record
                self.hostkit_db.delete_checkpoint(cp["id"])
                deleted_count += 1

            except Exception as e:
                errors.append({
                    "checkpoint_id": cp["id"],
                    "error": str(e),
                })

        return {
            "deleted_count": deleted_count,
            "freed_bytes": freed_bytes,
            "errors": errors,
            "cleaned_at": datetime.utcnow().isoformat(),
        }

    def get_latest_checkpoint(
        self,
        project_name: str,
        checkpoint_type: str | None = None,
    ) -> CheckpointInfo | None:
        """Get the most recent checkpoint for a project.

        Args:
            project_name: Name of the project
            checkpoint_type: Optional filter by type

        Returns:
            CheckpointInfo or None
        """
        cp = self.hostkit_db.get_latest_checkpoint(
            project_name=project_name,
            checkpoint_type=checkpoint_type,
        )

        if not cp:
            return None

        return CheckpointInfo(
            id=cp["id"],
            project_name=cp["project_name"],
            label=cp["label"],
            checkpoint_type=cp["checkpoint_type"],
            trigger_source=cp["trigger_source"],
            database_name=cp["database_name"],
            backup_path=cp["backup_path"],
            size_bytes=cp["size_bytes"],
            created_at=cp["created_at"],
            created_by=cp["created_by"],
            expires_at=cp["expires_at"],
        )

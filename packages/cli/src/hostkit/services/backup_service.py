"""Backup management service for HostKit."""

import configparser
import logging
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from hostkit.config import get_config
from hostkit.database import get_db

logger = logging.getLogger(__name__)

# R2 backup configuration
R2_BACKUP_BUCKET = "hostkit-backups"
R2_CONFIG_PATH = Path("/etc/hostkit/r2.ini")
R2_RETENTION_DAILY = 30  # Keep 30 daily backups in R2
R2_RETENTION_WEEKLY = 12  # Keep 12 weekly backups in R2


@dataclass
class BackupInfo:
    """Information about a backup."""

    id: str
    project: str
    backup_type: str  # "full", "db", "files", "credentials"
    path: str
    size_bytes: int
    created_at: str
    is_weekly: bool = False
    r2_synced: bool = False
    r2_key: str | None = None
    r2_synced_at: str | None = None
    local_exists: bool = True


@dataclass
class BackupVerificationResult:
    """Result of backup verification."""

    backup_id: str
    valid: bool
    checks: dict[str, bool]
    errors: list[str]


class BackupServiceError(Exception):
    """Base exception for backup service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


# Backup type components
BACKUP_COMPONENTS = {
    "full": ["database", "files", "env"],
    "db": ["database"],
    "files": ["files"],
    "credentials": ["env"],
}


class BackupService:
    """Service for managing backups across HostKit projects."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()
        self.backup_base = self.config.backup_dir
        self._r2_client: Any = None
        self._r2_endpoint: str | None = None

    # =========================================================================
    # R2 Cloud Backup Methods
    # =========================================================================

    def _get_r2_client(self) -> tuple[Any, str]:
        """Get boto3 S3 client configured for R2 backup bucket.

        Returns:
            Tuple of (boto3 client, endpoint_url)

        Raises:
            BackupServiceError: If R2 credentials not configured
        """
        if self._r2_client and self._r2_endpoint:
            return self._r2_client, self._r2_endpoint

        if not R2_CONFIG_PATH.exists():
            raise BackupServiceError(
                code="R2_BACKUP_NOT_CONFIGURED",
                message="R2 credentials not configured",
                suggestion="Create /etc/hostkit/r2.ini with Cloudflare R2 credentials",
            )

        try:
            config = configparser.ConfigParser()
            config.read(R2_CONFIG_PATH)

            account_id = config.get("cloudflare", "account_id")
            access_key_id = config.get("cloudflare", "access_key_id")
            secret_access_key = config.get("cloudflare", "secret_access_key")

            endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

            self._r2_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "adaptive"},
                ),
                region_name="auto",
            )
            self._r2_endpoint = endpoint_url

            return self._r2_client, self._r2_endpoint

        except (configparser.Error, KeyError) as e:
            raise BackupServiceError(
                code="R2_CONFIG_INVALID",
                message=f"Invalid R2 configuration: {e}",
                suggestion="Check /etc/hostkit/r2.ini format",
            )

    def _ensure_backup_bucket(self) -> None:
        """Ensure the hostkit-backups bucket exists."""
        client, _ = self._get_r2_client()

        try:
            client.head_bucket(Bucket=R2_BACKUP_BUCKET)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                # Bucket doesn't exist - create it
                client.create_bucket(Bucket=R2_BACKUP_BUCKET)
                logger.info(f"Created R2 backup bucket: {R2_BACKUP_BUCKET}")
            else:
                raise BackupServiceError(
                    code="R2_BUCKET_ERROR",
                    message=f"Error checking backup bucket: {e}",
                )

    def upload_to_r2(self, backup_id: str) -> dict[str, Any]:
        """Upload a local backup to R2.

        Args:
            backup_id: The backup ID to upload

        Returns:
            Dict with r2_key, size_bytes, upload_time

        Raises:
            BackupServiceError: If backup not found or upload fails
        """
        # Get backup record
        record = self.db.get_backup(backup_id)
        if not record:
            raise BackupServiceError(
                code="BACKUP_NOT_FOUND",
                message=f"Backup '{backup_id}' not found",
            )

        backup_path = Path(record["path"])
        if not backup_path.exists():
            raise BackupServiceError(
                code="BACKUP_FILE_MISSING",
                message=f"Backup file not found: {backup_path}",
                suggestion="The local backup file has been deleted",
            )

        # Ensure bucket exists
        self._ensure_backup_bucket()

        # Generate R2 key
        project = record["project"]
        filename = backup_path.name
        r2_key = f"{project}/{filename}"

        client, _ = self._get_r2_client()

        try:
            start_time = datetime.utcnow()

            # Upload file
            client.upload_file(
                str(backup_path),
                R2_BACKUP_BUCKET,
                r2_key,
                ExtraArgs={"ContentType": "application/gzip"},
            )

            upload_time = (datetime.utcnow() - start_time).total_seconds()
            synced_at = datetime.utcnow().isoformat()

            # Update database with R2 status
            self.db.update_backup_r2_status(
                backup_id=backup_id,
                r2_synced=True,
                r2_key=r2_key,
                r2_synced_at=synced_at,
            )

            return {
                "backup_id": backup_id,
                "r2_key": r2_key,
                "bucket": R2_BACKUP_BUCKET,
                "size_bytes": record["size_bytes"],
                "upload_time_seconds": round(upload_time, 2),
                "synced_at": synced_at,
            }

        except ClientError as e:
            raise BackupServiceError(
                code="R2_UPLOAD_FAILED",
                message=f"Failed to upload backup to R2: {e}",
                suggestion="Check R2 credentials and network connectivity",
            )

    def download_from_r2(
        self,
        backup_id: str | None = None,
        r2_key: str | None = None,
        dest_path: Path | None = None,
    ) -> dict[str, Any]:
        """Download a backup from R2.

        Can specify either backup_id (looks up r2_key from database)
        or r2_key directly (for R2-only backups).

        Args:
            backup_id: Backup ID to download (mutually exclusive with r2_key)
            r2_key: Direct R2 key to download (mutually exclusive with backup_id)
            dest_path: Destination path (defaults to standard backup location)

        Returns:
            Dict with local_path, size_bytes, download_time
        """
        if backup_id and r2_key:
            raise BackupServiceError(
                code="INVALID_ARGS",
                message="Specify either backup_id or r2_key, not both",
            )

        if not backup_id and not r2_key:
            raise BackupServiceError(
                code="INVALID_ARGS",
                message="Must specify either backup_id or r2_key",
            )

        # Get R2 key from database if backup_id provided
        if backup_id:
            record = self.db.get_backup(backup_id)
            if not record:
                raise BackupServiceError(
                    code="BACKUP_NOT_FOUND",
                    message=f"Backup '{backup_id}' not found",
                )
            if not record.get("r2_synced") or not record.get("r2_key"):
                raise BackupServiceError(
                    code="BACKUP_NOT_IN_R2",
                    message=f"Backup '{backup_id}' is not synced to R2",
                    suggestion="Use 'hostkit backup r2 sync' to upload it first",
                )
            r2_key = record["r2_key"]
            if dest_path is None:
                dest_path = Path(record["path"])

        # Determine destination path from r2_key if not provided
        if dest_path is None and r2_key:
            # Extract project and filename from key
            parts = r2_key.split("/", 1)
            if len(parts) == 2:
                project, filename = parts
                dest_path = self.backup_base / project / filename
            else:
                raise BackupServiceError(
                    code="INVALID_R2_KEY",
                    message=f"Invalid R2 key format: {r2_key}",
                )

        # Ensure destination directory exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        client, _ = self._get_r2_client()

        try:
            start_time = datetime.utcnow()

            # Download file
            client.download_file(R2_BACKUP_BUCKET, r2_key, str(dest_path))

            download_time = (datetime.utcnow() - start_time).total_seconds()
            size_bytes = dest_path.stat().st_size

            return {
                "r2_key": r2_key,
                "local_path": str(dest_path),
                "size_bytes": size_bytes,
                "download_time_seconds": round(download_time, 2),
            }

        except ClientError as e:
            raise BackupServiceError(
                code="R2_DOWNLOAD_FAILED",
                message=f"Failed to download backup from R2: {e}",
                suggestion="Check R2 key exists and credentials are valid",
            )

    def list_r2_backups(self, project: str | None = None) -> list[dict[str, Any]]:
        """List backups stored in R2.

        Args:
            project: Specific project to list, or None for all

        Returns:
            List of dicts with key, size, last_modified, project
        """
        client, _ = self._get_r2_client()

        try:
            # Check if bucket exists
            try:
                client.head_bucket(Bucket=R2_BACKUP_BUCKET)
            except ClientError:
                return []  # Bucket doesn't exist yet

            prefix = f"{project}/" if project else ""

            paginator = client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=R2_BACKUP_BUCKET, Prefix=prefix)

            backups = []
            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    parts = key.split("/", 1)
                    proj = parts[0] if len(parts) > 0 else "unknown"

                    backups.append(
                        {
                            "key": key,
                            "project": proj,
                            "size_bytes": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                        }
                    )

            return backups

        except ClientError as e:
            raise BackupServiceError(
                code="R2_LIST_FAILED",
                message=f"Failed to list R2 backups: {e}",
            )

    def rotate_r2_backups(self, project: str | None = None) -> dict[str, Any]:
        """Apply extended retention policy to R2 backups.

        Keeps 30 daily + 12 weekly backups.

        Args:
            project: Specific project to rotate, or None for all

        Returns:
            Dict with deleted_count, kept_daily, kept_weekly per project
        """
        r2_backups = self.list_r2_backups(project)

        # Group by project
        by_project: dict[str, list[dict[str, Any]]] = {}
        for backup in r2_backups:
            proj = backup["project"]
            if proj not in by_project:
                by_project[proj] = []
            by_project[proj].append(backup)

        client, _ = self._get_r2_client()
        results: dict[str, dict[str, Any]] = {}
        now = datetime.utcnow()

        for proj, proj_backups in by_project.items():
            deleted_count = 0
            kept_daily = 0
            kept_weekly = 0

            # Sort by last modified (newest first)
            proj_backups.sort(key=lambda x: x["last_modified"], reverse=True)

            for backup in proj_backups:
                modified = datetime.fromisoformat(
                    backup["last_modified"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
                age_days = (now - modified).days

                # Determine if it's a Monday backup
                is_monday = modified.weekday() == 0

                keep = False

                # Keep up to 30 daily backups
                if age_days < 30 and kept_daily < R2_RETENTION_DAILY:
                    keep = True
                    kept_daily += 1
                # Keep up to 12 weekly backups (Monday backups, older than 30 days)
                elif (
                    age_days >= 30
                    and age_days < 120
                    and is_monday
                    and kept_weekly < R2_RETENTION_WEEKLY
                ):
                    keep = True
                    kept_weekly += 1

                if not keep:
                    try:
                        client.delete_object(Bucket=R2_BACKUP_BUCKET, Key=backup["key"])
                        deleted_count += 1
                    except ClientError:
                        pass  # Log but continue

            results[proj] = {
                "deleted_count": deleted_count,
                "kept_daily": kept_daily,
                "kept_weekly": kept_weekly,
                "total_remaining": kept_daily + kept_weekly,
            }

        return {
            "projects": results,
            "total_deleted": sum(r["deleted_count"] for r in results.values()),
        }

    def get_r2_status(self) -> dict[str, Any]:
        """Get R2 backup storage status.

        Returns:
            Dict with bucket info, total size, object count
        """
        try:
            client, endpoint = self._get_r2_client()
        except BackupServiceError as e:
            return {
                "configured": False,
                "error": e.message,
            }

        try:
            client.head_bucket(Bucket=R2_BACKUP_BUCKET)
            bucket_exists = True
        except ClientError:
            bucket_exists = False

        if not bucket_exists:
            return {
                "configured": True,
                "endpoint": endpoint,
                "bucket": R2_BACKUP_BUCKET,
                "bucket_exists": False,
                "total_objects": 0,
                "total_size_bytes": 0,
            }

        # Get bucket stats
        backups = self.list_r2_backups()
        total_size = sum(b["size_bytes"] for b in backups)

        # Group by project
        by_project: dict[str, int] = {}
        for backup in backups:
            proj = backup["project"]
            by_project[proj] = by_project.get(proj, 0) + 1

        return {
            "configured": True,
            "endpoint": endpoint,
            "bucket": R2_BACKUP_BUCKET,
            "bucket_exists": True,
            "total_objects": len(backups),
            "total_size_bytes": total_size,
            "by_project": by_project,
        }

    # =========================================================================
    # Local Backup Methods
    # =========================================================================

    def _get_project_backup_dir(self, project: str) -> Path:
        """Get the backup directory for a project."""
        return self.backup_base / project

    def _validate_project(self, project: str) -> None:
        """Validate that a project exists."""
        proj = self.db.get_project(project)
        if not proj:
            raise BackupServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Run 'hostkit project list' to see available projects",
            )

    def _generate_backup_id(self, project: str, backup_type: str) -> str:
        """Generate a unique backup ID."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"{project}_{backup_type}_{timestamp}"

    def create_backup(
        self,
        project: str,
        backup_type: str = "full",
        upload_to_r2: bool = False,
    ) -> BackupInfo:
        """Create a backup of the specified type.

        Args:
            project: Project name
            backup_type: Type of backup (full, db, files, credentials)
            upload_to_r2: If True, also upload to R2 after local creation
        """
        self._validate_project(project)

        if backup_type not in BACKUP_COMPONENTS:
            raise BackupServiceError(
                code="INVALID_BACKUP_TYPE",
                message=f"Invalid backup type: {backup_type}",
                suggestion=f"Valid types: {', '.join(BACKUP_COMPONENTS.keys())}",
            )

        # Create backup directory
        backup_dir = self._get_project_backup_dir(project)
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup ID and path
        backup_id = self._generate_backup_id(project, backup_type)
        backup_path = backup_dir / f"{backup_id}.tar.gz"

        # Temporary directory for staging
        temp_dir = backup_dir / f".tmp_{backup_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            components = BACKUP_COMPONENTS[backup_type]

            # Collect backup components
            if "database" in components:
                self._backup_database(project, temp_dir)

            if "files" in components:
                self._backup_files(project, temp_dir)

            if "env" in components:
                self._backup_env(project, temp_dir)

            # Create tar archive
            with tarfile.open(backup_path, "w:gz") as tar:
                for item in temp_dir.iterdir():
                    tar.add(item, arcname=item.name)

            # Get backup size
            backup_size = backup_path.stat().st_size

            # Record in database
            self.db.create_backup_record(
                backup_id=backup_id,
                project=project,
                backup_type=backup_type,
                path=str(backup_path),
                size_bytes=backup_size,
            )

            # Initialize R2 fields
            r2_synced = False
            r2_key = None
            r2_synced_at = None

            # Upload to R2 if requested
            if upload_to_r2:
                try:
                    r2_result = self.upload_to_r2(backup_id)
                    r2_synced = True
                    r2_key = r2_result["r2_key"]
                    r2_synced_at = r2_result["synced_at"]
                except BackupServiceError as e:
                    # Log warning but don't fail - local backup is still valid
                    logger.warning(f"R2 upload failed for {backup_id}: {e.message}")

            return BackupInfo(
                id=backup_id,
                project=project,
                backup_type=backup_type,
                path=str(backup_path),
                size_bytes=backup_size,
                created_at=datetime.utcnow().isoformat(),
                r2_synced=r2_synced,
                r2_key=r2_key,
                r2_synced_at=r2_synced_at,
            )

        finally:
            # Clean up temp directory
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def _backup_database(self, project: str, temp_dir: Path) -> None:
        """Backup database using pg_dump."""
        db_name = f"{project}_db"
        dump_path = temp_dir / "database.sql"

        # Get admin credentials from environment
        admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        env = os.environ.copy()
        if admin_password:
            env["PGPASSWORD"] = admin_password

        pg_dump_cmd = [
            "pg_dump",
            "-h",
            self.config.postgres_host,
            "-p",
            str(self.config.postgres_port),
            "-U",
            admin_user,
            "-d",
            db_name,
            "--no-owner",
            "--no-acl",
            "-f",
            str(dump_path),
        ]

        try:
            result = subprocess.run(
                pg_dump_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                # Database might not exist - create empty marker
                dump_path.write_text(f"-- No database found for {project}\n")

        except subprocess.TimeoutExpired:
            raise BackupServiceError(
                code="BACKUP_TIMEOUT",
                message="Database backup timed out",
                suggestion="Try backing up the database separately",
            )
        except FileNotFoundError:
            raise BackupServiceError(
                code="PG_DUMP_NOT_FOUND",
                message="pg_dump command not found",
                suggestion="Ensure PostgreSQL client tools are installed",
            )

    def _backup_files(self, project: str, temp_dir: Path) -> None:
        """Backup application files."""
        app_dir = Path(f"/home/{project}/app")
        files_dir = temp_dir / "app"

        if app_dir.exists():
            shutil.copytree(app_dir, files_dir, symlinks=True)
        else:
            files_dir.mkdir(parents=True, exist_ok=True)
            (files_dir / ".empty").write_text("No app directory found")

    def _backup_env(self, project: str, temp_dir: Path) -> None:
        """Backup environment file."""
        env_path = Path(f"/home/{project}/.env")
        dest_path = temp_dir / ".env"

        if env_path.exists():
            shutil.copy2(env_path, dest_path)
        else:
            dest_path.write_text("# No .env file found\n")

    def list_backups(self, project: str | None = None) -> list[BackupInfo]:
        """List all backups, optionally filtered by project.

        Includes backups that exist locally, in R2, or both.
        """
        if project:
            self._validate_project(project)

        backup_records = self.db.list_backups(project)
        backups = []

        for record in backup_records:
            # Check if file still exists locally
            backup_path = Path(record["path"])
            local_exists = backup_path.exists()
            r2_synced = bool(record.get("r2_synced"))

            # Include if local OR R2 exists
            if local_exists or r2_synced:
                # Determine if this is a weekly backup
                created = datetime.fromisoformat(record["created_at"])
                is_weekly = created.weekday() == 0  # Monday

                backups.append(
                    BackupInfo(
                        id=record["id"],
                        project=record["project"],
                        backup_type=record["type"],
                        path=record["path"],
                        size_bytes=record["size_bytes"],
                        created_at=record["created_at"],
                        is_weekly=is_weekly,
                        r2_synced=r2_synced,
                        r2_key=record.get("r2_key"),
                        r2_synced_at=record.get("r2_synced_at"),
                        local_exists=local_exists,
                    )
                )

        return backups

    def get_backup(self, backup_id: str) -> BackupInfo | None:
        """Get a specific backup by ID.

        Returns backup info if local file exists OR if synced to R2.
        """
        record = self.db.get_backup(backup_id)
        if not record:
            return None

        backup_path = Path(record["path"])
        local_exists = backup_path.exists()
        r2_synced = bool(record.get("r2_synced"))

        # Return None only if neither local nor R2 exists
        if not local_exists and not r2_synced:
            return None

        created = datetime.fromisoformat(record["created_at"])
        return BackupInfo(
            id=record["id"],
            project=record["project"],
            backup_type=record["type"],
            path=record["path"],
            size_bytes=record["size_bytes"],
            created_at=record["created_at"],
            is_weekly=created.weekday() == 0,
            r2_synced=r2_synced,
            r2_key=record.get("r2_key"),
            r2_synced_at=record.get("r2_synced_at"),
            local_exists=local_exists,
        )

    def restore_backup(
        self,
        project: str,
        backup_id: str,
        restore_db: bool = True,
        restore_files: bool = True,
        restore_env: bool = False,
        from_r2: bool = False,
    ) -> dict[str, Any]:
        """Restore a project from backup.

        Args:
            project: Project name
            backup_id: Backup ID to restore
            restore_db: Restore database
            restore_files: Restore application files
            restore_env: Restore environment file
            from_r2: Download from R2 if local file missing
        """
        self._validate_project(project)

        backup = self.get_backup(backup_id)
        if not backup:
            raise BackupServiceError(
                code="BACKUP_NOT_FOUND",
                message=f"Backup '{backup_id}' not found",
                suggestion="Run 'hostkit backup list' to see available backups",
            )

        if backup.project != project:
            raise BackupServiceError(
                code="BACKUP_PROJECT_MISMATCH",
                message=f"Backup belongs to project '{backup.project}', not '{project}'",
                suggestion="Use the correct project name or backup ID",
            )

        backup_path = Path(backup.path)

        # Handle R2 download if local file missing
        if not backup_path.exists():
            if from_r2 and backup.r2_synced and backup.r2_key:
                # Download from R2
                logger.info(f"Downloading backup from R2: {backup.r2_key}")
                self.download_from_r2(backup_id=backup_id, dest_path=backup_path)
            else:
                suggestion = (
                    "Use --from-r2 to restore from cloud backup" if backup.r2_synced else None
                )
                raise BackupServiceError(
                    code="BACKUP_FILE_MISSING",
                    message=f"Backup file not found: {backup.path}",
                    suggestion=suggestion,
                )

        # Stop service before restore
        service_name = f"hostkit-{project}"
        try:
            subprocess.run(
                ["systemctl", "stop", service_name],
                capture_output=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            pass  # Service might not exist

        restored = {
            "database": False,
            "files": False,
            "env": False,
        }

        # Extract backup to temp directory
        temp_dir = self._get_project_backup_dir(project) / f".restore_{backup_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                tar.extractall(temp_dir)

            # Restore database
            if restore_db and (temp_dir / "database.sql").exists():
                self._restore_database(project, temp_dir / "database.sql")
                restored["database"] = True

            # Restore files
            if restore_files and (temp_dir / "app").exists():
                self._restore_files(project, temp_dir / "app")
                restored["files"] = True

            # Restore environment (only if explicitly requested)
            if restore_env and (temp_dir / ".env").exists():
                self._restore_env(project, temp_dir / ".env")
                restored["env"] = True

        finally:
            # Clean up temp directory
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

        # Restart service
        try:
            subprocess.run(
                ["systemctl", "start", service_name],
                capture_output=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return {
            "backup_id": backup_id,
            "project": project,
            "restored": restored,
            "restored_at": datetime.utcnow().isoformat(),
        }

    def _restore_database(self, project: str, dump_path: Path) -> None:
        """Restore database from dump file."""
        db_name = f"{project}_db"
        role_name = f"{project}_user"

        admin_user = os.environ.get("HOSTKIT_PG_ADMIN", "hostkit")
        admin_password = os.environ.get("HOSTKIT_PG_PASSWORD", "")

        env = os.environ.copy()
        if admin_password:
            env["PGPASSWORD"] = admin_password

        # Check if dump has content
        content = dump_path.read_text()
        if content.startswith("-- No database found"):
            return  # Nothing to restore

        # Drop and recreate database
        try:
            # Terminate connections
            subprocess.run(
                [
                    "psql",
                    "-h",
                    self.config.postgres_host,
                    "-p",
                    str(self.config.postgres_port),
                    "-U",
                    admin_user,
                    "-d",
                    "postgres",
                    "-c",
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    f" WHERE datname = '{db_name}'"
                    f" AND pid <> pg_backend_pid();",
                ],
                env=env,
                capture_output=True,
                timeout=30,
            )

            # Drop database
            subprocess.run(
                [
                    "psql",
                    "-h",
                    self.config.postgres_host,
                    "-p",
                    str(self.config.postgres_port),
                    "-U",
                    admin_user,
                    "-d",
                    "postgres",
                    "-c",
                    f"DROP DATABASE IF EXISTS {db_name};",
                ],
                env=env,
                capture_output=True,
                timeout=30,
            )

            # Create database
            subprocess.run(
                [
                    "psql",
                    "-h",
                    self.config.postgres_host,
                    "-p",
                    str(self.config.postgres_port),
                    "-U",
                    admin_user,
                    "-d",
                    "postgres",
                    "-c",
                    f"CREATE DATABASE {db_name} OWNER {role_name};",
                ],
                env=env,
                capture_output=True,
                timeout=30,
            )

            # Restore from dump
            subprocess.run(
                [
                    "psql",
                    "-h",
                    self.config.postgres_host,
                    "-p",
                    str(self.config.postgres_port),
                    "-U",
                    admin_user,
                    "-d",
                    db_name,
                    "-f",
                    str(dump_path),
                ],
                env=env,
                capture_output=True,
                timeout=300,
            )

        except subprocess.SubprocessError as e:
            raise BackupServiceError(
                code="RESTORE_DB_FAILED",
                message=f"Failed to restore database: {e}",
            )

    def _restore_files(self, project: str, source_dir: Path) -> None:
        """Restore application files."""
        app_dir = Path(f"/home/{project}/app")

        # Remove existing app directory
        if app_dir.exists():
            shutil.rmtree(app_dir)

        # Copy restored files
        shutil.copytree(source_dir, app_dir, symlinks=True)

        # Fix ownership
        subprocess.run(
            ["chown", "-R", f"{project}:{project}", str(app_dir)],
            capture_output=True,
        )

    def _restore_env(self, project: str, source_env: Path) -> None:
        """Restore environment file."""
        env_path = Path(f"/home/{project}/.env")

        # Backup current env first
        if env_path.exists():
            self.backup_credentials(project)

        # Copy restored env
        shutil.copy2(source_env, env_path)

        # Fix ownership and permissions
        subprocess.run(
            ["chown", f"{project}:{project}", str(env_path)],
            capture_output=True,
        )
        subprocess.run(["chmod", "600", str(env_path)], capture_output=True)

    def backup_credentials(self, project: str) -> dict[str, Any]:
        """Create a timestamped credential backup before changes."""
        self._validate_project(project)

        env_path = Path(f"/home/{project}/.env")
        if not env_path.exists():
            raise BackupServiceError(
                code="ENV_NOT_FOUND",
                message=f"Environment file not found for {project}",
            )

        # Create credentials backup directory
        creds_dir = self._get_project_backup_dir(project) / "credentials"
        creds_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup filename
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = creds_dir / f"env_{timestamp}.bak"

        # Copy env file
        shutil.copy2(env_path, backup_path)

        # Rotate credential backups (keep last 10)
        cred_backups = sorted(creds_dir.glob("env_*.bak"), reverse=True)
        for old_backup in cred_backups[10:]:
            old_backup.unlink()

        return {
            "project": project,
            "backup_path": str(backup_path),
            "created_at": datetime.utcnow().isoformat(),
        }

    def rotate_backups(self, project: str) -> dict[str, Any]:
        """Apply retention policy: keep 7 daily + 4 weekly backups."""
        self._validate_project(project)

        backups = self.list_backups(project)
        now = datetime.utcnow()

        deleted_count = 0
        kept_daily = 0
        kept_weekly = 0

        # Sort by creation date (newest first)
        backups.sort(key=lambda x: x.created_at, reverse=True)

        for backup in backups:
            created = datetime.fromisoformat(backup.created_at)
            age_days = (now - created).days

            keep = False

            # Keep up to 7 daily backups (last 7 days)
            if age_days < 7 and kept_daily < 7:
                keep = True
                kept_daily += 1
            # Keep up to 4 weekly backups (Monday backups, older than 7 days)
            elif age_days >= 7 and age_days < 35 and backup.is_weekly and kept_weekly < 4:
                keep = True
                kept_weekly += 1

            if not keep:
                self.delete_backup(backup.id)
                deleted_count += 1

        return {
            "project": project,
            "deleted_count": deleted_count,
            "kept_daily": kept_daily,
            "kept_weekly": kept_weekly,
            "total_remaining": kept_daily + kept_weekly,
        }

    def delete_backup(self, backup_id: str) -> bool:
        """Delete a backup file and its database record."""
        backup = self.get_backup(backup_id)
        if not backup:
            return False

        # Delete file
        backup_path = Path(backup.path)
        if backup_path.exists():
            backup_path.unlink()

        # Delete database record
        self.db.delete_backup_record(backup_id)

        return True

    def verify_backup(self, backup_id: str) -> BackupVerificationResult:
        """Verify backup integrity."""
        backup = self.get_backup(backup_id)
        if not backup:
            return BackupVerificationResult(
                backup_id=backup_id,
                valid=False,
                checks={},
                errors=["Backup not found"],
            )

        checks = {
            "file_exists": False,
            "can_decompress": False,
            "has_manifest": False,
            "database_valid": False,
        }
        errors = []

        backup_path = Path(backup.path)

        # Check file exists
        if backup_path.exists():
            checks["file_exists"] = True
        else:
            errors.append("Backup file does not exist")
            return BackupVerificationResult(
                backup_id=backup_id,
                valid=False,
                checks=checks,
                errors=errors,
            )

        # Try to decompress and inspect
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                checks["can_decompress"] = True
                members = tar.getnames()

                # Check for expected files based on type
                components = BACKUP_COMPONENTS.get(backup.backup_type, [])
                if "database" in components and "database.sql" in members:
                    checks["database_valid"] = True
                elif "database" not in components:
                    checks["database_valid"] = True  # N/A

                if "files" in components and "app" in members:
                    checks["has_manifest"] = True
                elif "files" not in components:
                    checks["has_manifest"] = True  # N/A

                if "env" in components and ".env" in members:
                    if not checks["has_manifest"]:
                        checks["has_manifest"] = True
                elif "env" not in components and "files" not in components:
                    checks["has_manifest"] = True  # N/A

        except tarfile.TarError as e:
            errors.append(f"Failed to decompress: {e}")
        except Exception as e:
            errors.append(f"Unexpected error: {e}")

        valid = all(checks.values()) and len(errors) == 0

        return BackupVerificationResult(
            backup_id=backup_id,
            valid=valid,
            checks=checks,
            errors=errors,
        )

    def get_backup_stats(self, project: str | None = None) -> dict[str, Any]:
        """Get backup statistics."""
        backups = self.list_backups(project)

        total_size = sum(b.size_bytes for b in backups)
        total_count = len(backups)

        # Group by project
        by_project: dict[str, list[BackupInfo]] = {}
        for backup in backups:
            if backup.project not in by_project:
                by_project[backup.project] = []
            by_project[backup.project].append(backup)

        project_stats = {}
        for proj, proj_backups in by_project.items():
            project_stats[proj] = {
                "count": len(proj_backups),
                "size_bytes": sum(b.size_bytes for b in proj_backups),
                "latest": max(b.created_at for b in proj_backups) if proj_backups else None,
            }

        return {
            "total_backups": total_count,
            "total_size_bytes": total_size,
            "by_project": project_stats,
        }

    def export_backup(self, backup_id: str, dest_path: str) -> dict[str, Any]:
        """Copy backup to a destination path for export."""
        backup = self.get_backup(backup_id)
        if not backup:
            raise BackupServiceError(
                code="BACKUP_NOT_FOUND",
                message=f"Backup '{backup_id}' not found",
            )

        source = Path(backup.path)
        dest = Path(dest_path)

        if not source.exists():
            raise BackupServiceError(
                code="BACKUP_FILE_MISSING",
                message="Backup file not found on disk",
            )

        # Create destination directory if needed
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Copy file
        shutil.copy2(source, dest)

        return {
            "backup_id": backup_id,
            "source": str(source),
            "destination": str(dest),
            "size_bytes": dest.stat().st_size,
            "exported_at": datetime.utcnow().isoformat(),
        }

    def create_all_backups(
        self,
        backup_type: str = "full",
        upload_to_r2: bool = False,
    ) -> list[BackupInfo]:
        """Create backups for all projects (for scheduled backups).

        Args:
            backup_type: Type of backup (full, db, files)
            upload_to_r2: If True, also upload each backup to R2
        """
        projects = self.db.list_projects()
        results = []

        for project in projects:
            try:
                backup = self.create_backup(
                    project["name"],
                    backup_type,
                    upload_to_r2=upload_to_r2,
                )
                results.append(backup)
            except BackupServiceError:
                # Log but continue with other projects
                continue

        return results

    def rotate_all_backups(self) -> dict[str, Any]:
        """Apply retention policy to all projects."""
        projects = self.db.list_projects()
        results = {}

        for project in projects:
            try:
                result = self.rotate_backups(project["name"])
                results[project["name"]] = result
            except BackupServiceError:
                continue

        return results

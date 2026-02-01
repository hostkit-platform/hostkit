"""Cloudflare R2 object storage management for HostKit.

Provides per-project R2 buckets with S3-compatible access.
Each project gets its own bucket named 'hostkit-{project}'.
"""

import configparser
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta


# Register R2 service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="r2",
        description="Cloudflare R2 object storage (S3-compatible, zero egress)",
        provision_flag="--with-r2",
        enable_command="hostkit r2 enable {project}",
        env_vars_provided=[
            "R2_ENDPOINT",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET",
        ],
        related_commands=[
            "r2 enable",
            "r2 disable",
            "r2 status",
            "r2 upload",
            "r2 download",
            "r2 list",
            "r2 delete",
            "r2 presign",
        ],
    )
)


R2_CONFIG_PATH = Path("/etc/hostkit/r2.ini")


@dataclass
class R2Credentials:
    """R2 access credentials."""

    account_id: str
    access_key_id: str
    secret_access_key: str

    @property
    def endpoint_url(self) -> str:
        """S3-compatible endpoint URL."""
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


@dataclass
class R2Object:
    """Information about an R2 object."""

    key: str
    size: int
    last_modified: datetime
    etag: str
    content_type: str | None = None


class R2ServiceError(Exception):
    """Base exception for R2 service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class R2Service:
    """Service for managing Cloudflare R2 object storage."""

    def __init__(self) -> None:
        """Initialize the R2 service."""
        self.config = get_config()
        self.db = get_db()
        self._credentials: R2Credentials | None = None
        self._client: Any = None

    # =========================================================================
    # Credential Management
    # =========================================================================

    def _load_credentials(self) -> R2Credentials:
        """Load R2 credentials from /etc/hostkit/r2.ini."""
        if self._credentials:
            return self._credentials

        if not R2_CONFIG_PATH.exists():
            raise R2ServiceError(
                code="R2_NOT_CONFIGURED",
                message="R2 credentials not configured",
                suggestion="Create /etc/hostkit/r2.ini with Cloudflare R2 credentials",
            )

        try:
            config = configparser.ConfigParser()
            config.read(R2_CONFIG_PATH)

            self._credentials = R2Credentials(
                account_id=config.get("cloudflare", "account_id"),
                access_key_id=config.get("cloudflare", "access_key_id"),
                secret_access_key=config.get("cloudflare", "secret_access_key"),
            )
            return self._credentials

        except (configparser.Error, KeyError) as e:
            raise R2ServiceError(
                code="R2_CONFIG_INVALID",
                message=f"Invalid R2 configuration: {e}",
                suggestion="Check /etc/hostkit/r2.ini format",
            )

    def _get_client(self) -> Any:
        """Get boto3 S3 client configured for R2."""
        if self._client:
            return self._client

        creds = self._load_credentials()

        self._client = boto3.client(
            "s3",
            endpoint_url=creds.endpoint_url,
            aws_access_key_id=creds.access_key_id,
            aws_secret_access_key=creds.secret_access_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
            region_name="auto",
        )
        return self._client

    # =========================================================================
    # Bucket Naming
    # =========================================================================

    def _bucket_name(self, project: str) -> str:
        """Generate bucket name for a project."""
        return f"hostkit-{project}"

    # =========================================================================
    # Enable/Disable
    # =========================================================================

    def enable(self, project: str) -> dict[str, Any]:
        """Enable R2 storage for a project.

        Creates a bucket and injects credentials into project .env.

        Returns:
            Dict with bucket name, endpoint, and status.
        """
        # Validate project exists
        project_data = self.db.get_project(project)
        if not project_data:
            raise R2ServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        bucket = self._bucket_name(project)
        client = self._get_client()
        creds = self._load_credentials()

        # Check if already enabled
        if self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_ALREADY_ENABLED",
                message=f"R2 is already enabled for '{project}'",
                suggestion="Use 'hostkit r2 status' to check configuration",
            )

        # Create bucket
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise R2ServiceError(
                code="R2_CREATE_FAILED",
                message=f"Failed to create R2 bucket: {error_code}",
                suggestion="Check R2 credentials and permissions",
            )

        # Inject environment variables
        self._update_project_env(project, bucket, creds)

        return {
            "project": project,
            "bucket": bucket,
            "endpoint": creds.endpoint_url,
        }

    def disable(self, project: str, force: bool = False) -> dict[str, Any]:
        """Disable R2 storage for a project.

        Deletes all objects and the bucket.
        """
        if not force:
            raise R2ServiceError(
                code="FORCE_REQUIRED",
                message="The --force flag is required to disable R2 storage",
                suggestion=f"Add --force to confirm: 'hostkit r2 disable {project} --force'",
            )

        # Validate project exists
        project_data = self.db.get_project(project)
        if not project_data:
            raise R2ServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Check the project name with 'hostkit project list'",
            )

        bucket = self._bucket_name(project)
        client = self._get_client()

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        # Delete all objects first (required before bucket deletion)
        self._empty_bucket(bucket)

        # Delete bucket
        try:
            client.delete_bucket(Bucket=bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise R2ServiceError(
                code="R2_DELETE_FAILED",
                message=f"Failed to delete R2 bucket: {error_code}",
            )

        # Remove environment variables
        self._remove_project_env(project)

        return {
            "project": project,
            "bucket": bucket,
            "deleted": True,
        }

    def is_enabled(self, project: str) -> bool:
        """Check if R2 is enabled for a project."""
        bucket = self._bucket_name(project)
        return self._bucket_exists(bucket)

    # =========================================================================
    # Status
    # =========================================================================

    def status(self, project: str) -> dict[str, Any]:
        """Get R2 status for a project."""
        project_data = self.db.get_project(project)
        if not project_data:
            raise R2ServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
            )

        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            return {
                "enabled": False,
                "project": project,
                "bucket": None,
            }

        creds = self._load_credentials()

        # Get object count and total size
        object_count, total_size = self._get_bucket_stats(bucket)

        return {
            "enabled": True,
            "project": project,
            "bucket": bucket,
            "endpoint": creds.endpoint_url,
            "object_count": object_count,
            "total_size_bytes": total_size,
            "total_size_human": self._format_size(total_size),
        }

    # =========================================================================
    # Object Operations
    # =========================================================================

    def upload(
        self,
        project: str,
        local_path: str,
        remote_key: str,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to the project's R2 bucket."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
                suggestion=f"Enable with: hostkit r2 enable {project}",
            )

        path = Path(local_path)
        if not path.exists():
            raise R2ServiceError(
                code="FILE_NOT_FOUND",
                message=f"File not found: {local_path}",
            )

        # Determine content type
        if not content_type:
            content_type, _ = mimetypes.guess_type(local_path)
            content_type = content_type or "application/octet-stream"

        client = self._get_client()

        try:
            with open(local_path, "rb") as f:
                client.upload_fileobj(
                    f,
                    bucket,
                    remote_key,
                    ExtraArgs={"ContentType": content_type},
                )
        except ClientError as e:
            raise R2ServiceError(
                code="UPLOAD_FAILED",
                message=f"Failed to upload file: {e}",
            )

        return {
            "project": project,
            "bucket": bucket,
            "key": remote_key,
            "size": path.stat().st_size,
            "content_type": content_type,
        }

    def download(
        self,
        project: str,
        remote_key: str,
        local_path: str,
    ) -> dict[str, Any]:
        """Download a file from the project's R2 bucket."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        client = self._get_client()

        try:
            client.download_file(bucket, remote_key, local_path)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise R2ServiceError(
                    code="OBJECT_NOT_FOUND",
                    message=f"Object not found: {remote_key}",
                )
            raise R2ServiceError(
                code="DOWNLOAD_FAILED",
                message=f"Failed to download file: {e}",
            )

        return {
            "project": project,
            "bucket": bucket,
            "key": remote_key,
            "local_path": local_path,
        }

    def list_objects(
        self,
        project: str,
        prefix: str | None = None,
        max_keys: int = 1000,
    ) -> list[R2Object]:
        """List objects in the project's R2 bucket."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        client = self._get_client()
        objects: list[R2Object] = []

        try:
            kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": max_keys}
            if prefix:
                kwargs["Prefix"] = prefix

            response = client.list_objects_v2(**kwargs)

            for obj in response.get("Contents", []):
                objects.append(
                    R2Object(
                        key=obj["Key"],
                        size=obj["Size"],
                        last_modified=obj["LastModified"],
                        etag=obj["ETag"].strip('"'),
                    )
                )

        except ClientError as e:
            raise R2ServiceError(
                code="LIST_FAILED",
                message=f"Failed to list objects: {e}",
            )

        return objects

    def delete_object(
        self,
        project: str,
        key: str,
    ) -> dict[str, Any]:
        """Delete an object from the project's R2 bucket."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        client = self._get_client()

        try:
            client.delete_object(Bucket=bucket, Key=key)
        except ClientError as e:
            raise R2ServiceError(
                code="DELETE_FAILED",
                message=f"Failed to delete object: {e}",
            )

        return {
            "project": project,
            "bucket": bucket,
            "key": key,
            "deleted": True,
        }

    def generate_presigned_url(
        self,
        project: str,
        key: str,
        expires: int = 3600,
        method: str = "GET",
    ) -> dict[str, Any]:
        """Generate a presigned URL for an object."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        client = self._get_client()

        client_method = "get_object" if method == "GET" else "put_object"

        try:
            url = client.generate_presigned_url(
                ClientMethod=client_method,
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires,
            )
        except ClientError as e:
            raise R2ServiceError(
                code="PRESIGN_FAILED",
                message=f"Failed to generate presigned URL: {e}",
            )

        return {
            "project": project,
            "bucket": bucket,
            "key": key,
            "method": method,
            "url": url,
            "expires_in": expires,
        }

    # =========================================================================
    # Usage
    # =========================================================================

    def get_usage(self) -> dict[str, Any]:
        """Get R2 usage across all projects."""
        client = self._get_client()

        # List all hostkit-* buckets
        try:
            response = client.list_buckets()
        except ClientError as e:
            raise R2ServiceError(
                code="LIST_BUCKETS_FAILED",
                message=f"Failed to list buckets: {e}",
            )

        projects: list[dict[str, Any]] = []
        total_objects = 0
        total_size = 0

        for bucket_info in response.get("Buckets", []):
            name = bucket_info["Name"]
            if not name.startswith("hostkit-"):
                continue

            project = name[8:]  # Remove "hostkit-" prefix
            obj_count, size = self._get_bucket_stats(name)

            projects.append(
                {
                    "project": project,
                    "bucket": name,
                    "object_count": obj_count,
                    "size_bytes": size,
                    "size_human": self._format_size(size),
                }
            )

            total_objects += obj_count
            total_size += size

        return {
            "project_count": len(projects),
            "total_objects": total_objects,
            "total_size_bytes": total_size,
            "total_size_human": self._format_size(total_size),
            "projects": projects,
        }

    def get_credentials(self, project: str, env_format: bool = False) -> dict[str, Any]:
        """Get S3 credentials for a project."""
        bucket = self._bucket_name(project)

        if not self._bucket_exists(bucket):
            raise R2ServiceError(
                code="R2_NOT_ENABLED",
                message=f"R2 is not enabled for '{project}'",
            )

        creds = self._load_credentials()

        result: dict[str, Any] = {
            "project": project,
            "bucket": bucket,
            "endpoint": creds.endpoint_url,
            "access_key_id": creds.access_key_id,
            "secret_access_key": creds.secret_access_key,
            "region": "auto",
        }

        if env_format:
            result["env_format"] = (
                f"R2_ENDPOINT={creds.endpoint_url}\n"
                f"R2_ACCESS_KEY_ID={creds.access_key_id}\n"
                f"R2_SECRET_ACCESS_KEY={creds.secret_access_key}\n"
                f"R2_BUCKET={bucket}\n"
            )

        return result

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _bucket_exists(self, bucket: str) -> bool:
        """Check if a bucket exists."""
        client = self._get_client()
        try:
            client.head_bucket(Bucket=bucket)
            return True
        except ClientError:
            return False

    def _empty_bucket(self, bucket: str) -> None:
        """Delete all objects in a bucket."""
        client = self._get_client()

        # List and delete in batches
        paginator = client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket):
            objects = page.get("Contents", [])
            if not objects:
                continue

            delete_keys = [{"Key": obj["Key"]} for obj in objects]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": delete_keys},
            )

    def _get_bucket_stats(self, bucket: str) -> tuple[int, int]:
        """Get object count and total size for a bucket."""
        client = self._get_client()

        total_objects = 0
        total_size = 0

        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    total_objects += 1
                    total_size += obj["Size"]
        except ClientError:
            pass

        return total_objects, total_size

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human-readable string."""
        size = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def _update_project_env(
        self,
        project: str,
        bucket: str,
        creds: R2Credentials,
    ) -> None:
        """Update project's .env with R2 credentials."""
        from hostkit.services.env_service import EnvService

        env_service = EnvService()

        # Set R2-specific env vars
        env_service.set_env(project, "R2_ENDPOINT", creds.endpoint_url)
        env_service.set_env(project, "R2_ACCESS_KEY_ID", creds.access_key_id)
        env_service.set_env(project, "R2_SECRET_ACCESS_KEY", creds.secret_access_key)
        env_service.set_env(project, "R2_BUCKET", bucket)

        # Also set AWS-compatible vars for libraries that expect them
        env_service.set_env(project, "AWS_ENDPOINT_URL_S3", creds.endpoint_url)
        env_service.set_env(project, "AWS_ACCESS_KEY_ID", creds.access_key_id)
        env_service.set_env(project, "AWS_SECRET_ACCESS_KEY", creds.secret_access_key)
        env_service.set_env(project, "AWS_DEFAULT_REGION", "auto")

    def _remove_project_env(self, project: str) -> None:
        """Remove R2 credentials from project's .env."""
        from hostkit.services.env_service import EnvService

        env_service = EnvService()

        for key in [
            "R2_ENDPOINT",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET",
            "AWS_ENDPOINT_URL_S3",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION",
        ]:
            try:
                env_service.unset_env(project, key)
            except Exception:
                pass  # Ignore if var doesn't exist

"""MinIO object storage management for HostKit."""

import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hostkit.config import get_config
from hostkit.database import get_db
from hostkit.registry import CapabilitiesRegistry, ServiceMeta

# Register storage service with capabilities registry
CapabilitiesRegistry.register_service(
    ServiceMeta(
        name="minio",
        description="S3-compatible object storage (MinIO) - https://s3.hostkit.dev",
        provision_flag="--with-minio",
        enable_command="hostkit minio enable {project}",
        env_vars_provided=[
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
            "S3_PUBLIC_URL",
        ],
        related_commands=[
            "minio enable",
            "minio disable",
            "minio status",
            "minio policy",
            "minio upload",
            "storage list",
            "storage upload",
        ],
    )
)


# MinIO configuration paths
MINIO_BINARY = "/usr/local/bin/minio"
MINIO_DATA_DIR = "/var/lib/minio/data"
MINIO_CONFIG = "/etc/default/minio"
MINIO_SERVICE = "/etc/systemd/system/minio.service"
MC_BINARY = "/usr/local/bin/mc"
MC_ALIAS = "hostkit"  # Alias for MinIO admin operations
STORAGE_CONFIG_PATH = Path("/etc/hostkit/storage.yaml")


@dataclass
class BucketInfo:
    """Information about a storage bucket."""

    name: str
    project: str | None
    size: str
    objects: int
    created_at: str | None = None


@dataclass
class S3Credentials:
    """S3 access credentials for a project."""

    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"
    public_url: str | None = None

    def to_env_format(self) -> str:
        """Format credentials for .env file."""
        lines = f"""# MinIO S3 Storage
S3_ENDPOINT={self.endpoint}
S3_BUCKET={self.bucket}
S3_ACCESS_KEY={self.access_key}
S3_SECRET_KEY={self.secret_key}
S3_REGION={self.region}
AWS_ACCESS_KEY_ID={self.access_key}
AWS_SECRET_ACCESS_KEY={self.secret_key}
"""
        if self.public_url:
            lines += f"S3_PUBLIC_URL={self.public_url}\n"
        return lines


class StorageServiceError(Exception):
    """Base exception for storage service errors."""

    def __init__(self, code: str, message: str, suggestion: str | None = None):
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)


class StorageService:
    """Service for managing MinIO object storage."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = get_db()

    def _get_storage_config(self) -> dict[str, Any]:
        """Load storage configuration."""
        if not STORAGE_CONFIG_PATH.exists():
            return {}
        return yaml.safe_load(STORAGE_CONFIG_PATH.read_text()) or {}

    def _save_storage_config(self, config: dict[str, Any]) -> None:
        """Save storage configuration."""
        STORAGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        STORAGE_CONFIG_PATH.write_text(yaml.safe_dump(config, default_flow_style=False))

    def _get_minio_endpoint(self, public: bool = False) -> str:
        """Get the MinIO endpoint URL.

        Args:
            public: If True, return the public-facing URL (via nginx proxy).
                   If False, return the internal localhost URL.
        """
        if public:
            # Default public endpoint is s3.hostkit.dev
            default_public = "https://s3.hostkit.dev"

            # Check if s3.hostkit.dev is configured
            s3_config = Path("/etc/nginx/sites-enabled/s3.hostkit.dev")
            if s3_config.exists():
                return default_public

            # Fall back to legacy minio-proxy config
            proxy_config = Path("/etc/nginx/sites-available/minio-proxy")
            if proxy_config.exists():
                try:
                    content = proxy_config.read_text()
                    # Parse server_name from nginx config
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("server_name"):
                            domain = line.split()[1].rstrip(";")
                            # Check if SSL is configured
                            if "listen 443" in content:
                                return f"https://{domain}"
                            return f"http://{domain}"
                except (OSError, IndexError):
                    pass
            # No proxy configured, return None to indicate no public URL
            return None
        return "http://localhost:9000"

    def _get_root_credentials(self) -> tuple[str, str]:
        """Get MinIO root credentials from config file."""
        try:
            env_content = Path(MINIO_CONFIG).read_text()
            access_key = ""
            secret_key = ""
            for line in env_content.splitlines():
                if line.startswith("MINIO_ROOT_USER="):
                    access_key = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("MINIO_ROOT_PASSWORD="):
                    secret_key = line.split("=", 1)[1].strip().strip('"')
            if access_key and secret_key:
                return access_key, secret_key
        except (OSError, ValueError):
            pass

        raise StorageServiceError(
            code="MINIO_NOT_CONFIGURED",
            message="MinIO root credentials not found",
            suggestion="Run 'hostkit storage setup' to configure MinIO",
        )

    def _mc_command(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a MinIO client (mc) command."""
        cmd = [MC_BINARY] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if check and result.returncode != 0:
                raise StorageServiceError(
                    code="MC_COMMAND_FAILED",
                    message=f"MinIO client command failed: {result.stderr or result.stdout}",
                )
            return result
        except FileNotFoundError:
            raise StorageServiceError(
                code="MC_NOT_FOUND",
                message="MinIO client (mc) not found",
                suggestion="Run 'hostkit storage setup' to install MinIO tools",
            )
        except subprocess.TimeoutExpired:
            raise StorageServiceError(
                code="MC_TIMEOUT",
                message="MinIO client command timed out",
            )

    def _ensure_mc_alias(self) -> None:
        """Ensure the mc alias is configured for admin operations."""
        access_key, secret_key = self._get_root_credentials()
        endpoint = self._get_minio_endpoint()

        # Set up alias (overwrites if exists)
        self._mc_command(
            [
                "alias",
                "set",
                MC_ALIAS,
                endpoint,
                access_key,
                secret_key,
            ]
        )

    def is_minio_installed(self) -> bool:
        """Check if MinIO server is installed."""
        return Path(MINIO_BINARY).exists()

    def is_minio_running(self) -> bool:
        """Check if MinIO service is running."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "minio"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() == "active"
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def get_status(self) -> dict[str, Any]:
        """Get MinIO service status."""
        installed = self.is_minio_installed()
        running = self.is_minio_running() if installed else False

        status = {
            "installed": installed,
            "running": running,
            "endpoint": self._get_minio_endpoint() if running else None,
            "console_url": "http://localhost:9001" if running else None,
            "data_dir": MINIO_DATA_DIR,
        }

        # Get storage config
        config = self._get_storage_config()
        if config.get("buckets"):
            status["bucket_count"] = len(config["buckets"])

        return status

    def setup(self, root_password: str | None = None) -> dict[str, Any]:
        """Install and configure MinIO.

        Args:
            root_password: Optional root password. Generated if not provided.

        Returns:
            Setup result with credentials.
        """
        # Generate root credentials
        root_user = "minioadmin"
        root_pass = root_password or secrets.token_urlsafe(32)

        # 1. Download MinIO binary if not exists
        if not Path(MINIO_BINARY).exists():
            try:
                subprocess.run(
                    [
                        "wget",
                        "-q",
                        "https://dl.min.io/server/minio/release/linux-amd64/minio",
                        "-O",
                        MINIO_BINARY,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                subprocess.run(["chmod", "+x", MINIO_BINARY], check=True)
            except subprocess.CalledProcessError as e:
                raise StorageServiceError(
                    code="MINIO_DOWNLOAD_FAILED",
                    message=f"Failed to download MinIO: {e.stderr}",
                    suggestion="Check network connectivity and try again",
                )

        # 2. Download mc (MinIO client) if not exists
        if not Path(MC_BINARY).exists():
            try:
                subprocess.run(
                    [
                        "wget",
                        "-q",
                        "https://dl.min.io/client/mc/release/linux-amd64/mc",
                        "-O",
                        MC_BINARY,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                subprocess.run(["chmod", "+x", MC_BINARY], check=True)
            except subprocess.CalledProcessError as e:
                raise StorageServiceError(
                    code="MC_DOWNLOAD_FAILED",
                    message=f"Failed to download MinIO client: {e.stderr}",
                    suggestion="Check network connectivity and try again",
                )

        # 3. Create minio system user
        try:
            subprocess.run(
                ["id", "minio"],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            # User doesn't exist, create it
            subprocess.run(
                [
                    "useradd",
                    "--system",
                    "--no-create-home",
                    "--shell",
                    "/bin/false",
                    "minio",
                ],
                check=True,
                capture_output=True,
            )

        # 4. Create data directory
        data_path = Path(MINIO_DATA_DIR)
        data_path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["chown", "-R", "minio:minio", str(data_path)],
            check=True,
        )

        # 5. Create environment file
        env_content = f"""# MinIO Configuration - Managed by HostKit
MINIO_ROOT_USER="{root_user}"
MINIO_ROOT_PASSWORD="{root_pass}"
MINIO_VOLUMES="{MINIO_DATA_DIR}"
MINIO_OPTS="--console-address :9001"
"""
        Path(MINIO_CONFIG).write_text(env_content)
        subprocess.run(["chmod", "600", MINIO_CONFIG], check=True)

        # 6. Create systemd service
        service_content = """[Unit]
Description=MinIO Object Storage
Documentation=https://min.io/docs/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=minio
Group=minio
EnvironmentFile=/etc/default/minio
ExecStart=/usr/local/bin/minio server $MINIO_VOLUMES $MINIO_OPTS
Restart=always
RestartSec=10
LimitNOFILE=65536

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/minio

[Install]
WantedBy=multi-user.target
"""
        Path(MINIO_SERVICE).write_text(service_content)

        # 7. Reload systemd and start service
        subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
        subprocess.run(["systemctl", "enable", "minio"], check=True, capture_output=True)
        subprocess.run(["systemctl", "start", "minio"], check=True, capture_output=True)

        # Wait a moment for service to start
        import time

        time.sleep(2)

        # 8. Configure mc alias
        try:
            self._ensure_mc_alias()
        except StorageServiceError:
            pass  # Might fail if service is still starting

        # 9. Initialize storage config
        storage_config = self._get_storage_config()
        storage_config["initialized"] = True
        storage_config["buckets"] = storage_config.get("buckets", {})
        self._save_storage_config(storage_config)

        return {
            "installed": True,
            "running": self.is_minio_running(),
            "endpoint": self._get_minio_endpoint(),
            "console_url": "http://localhost:9001",
            "root_user": root_user,
            "root_password": root_pass,
            "data_dir": MINIO_DATA_DIR,
        }

    def list_buckets(self) -> list[BucketInfo]:
        """List all storage buckets."""
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion=(
                    "Start MinIO with 'systemctl start minio' or run 'hostkit storage setup'"
                ),
            )

        self._ensure_mc_alias()

        # Get bucket list
        result = self._mc_command(["ls", f"{MC_ALIAS}/"], check=False)
        if result.returncode != 0:
            return []

        # Load storage config for project mappings
        config = self._get_storage_config()
        bucket_projects = config.get("buckets", {})

        buckets = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            # Parse mc ls output: [date] [size] bucket_name
            parts = line.split()
            if len(parts) >= 3:
                bucket_name = parts[-1].rstrip("/")
                # Get bucket info
                bucket_info = self._get_bucket_info(bucket_name)
                bucket_info.project = bucket_projects.get(bucket_name)
                buckets.append(bucket_info)

        return buckets

    def _get_bucket_info(self, bucket_name: str) -> BucketInfo:
        """Get detailed information about a bucket."""
        # Get bucket size and object count
        result = self._mc_command(["du", f"{MC_ALIAS}/{bucket_name}"], check=False)

        size = "0 B"
        objects = 0

        if result.returncode == 0 and result.stdout.strip():
            # Parse du output: size  bucket
            parts = result.stdout.strip().split()
            if parts:
                size = parts[0]

        # Get object count
        result = self._mc_command(
            ["find", f"{MC_ALIAS}/{bucket_name}", "--maxdepth", "0"], check=False
        )
        if result.returncode == 0:
            # Count lines (each line is an object)
            lines = [
                line
                for line in result.stdout.strip().splitlines()
                if line and not line.endswith("/")
            ]
            objects = len(lines)

        return BucketInfo(
            name=bucket_name,
            project=None,
            size=size,
            objects=objects,
        )

    def create_bucket(
        self,
        bucket_name: str,
        project: str | None = None,
    ) -> S3Credentials:
        """Create a new storage bucket with access credentials.

        Args:
            bucket_name: Name for the bucket.
            project: Optional project to associate with.

        Returns:
            S3 credentials for accessing the bucket.
        """
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion=(
                    "Start MinIO with 'systemctl start minio' or run 'hostkit storage setup'"
                ),
            )

        # Validate bucket name
        if not bucket_name.replace("-", "").replace("_", "").isalnum():
            raise StorageServiceError(
                code="INVALID_BUCKET_NAME",
                message=f"Invalid bucket name: {bucket_name}",
                suggestion=(
                    "Bucket names can only contain letters, numbers, hyphens and underscores"
                ),
            )

        self._ensure_mc_alias()

        # Check if bucket exists
        result = self._mc_command(["ls", f"{MC_ALIAS}/{bucket_name}"], check=False)
        if result.returncode == 0:
            raise StorageServiceError(
                code="BUCKET_EXISTS",
                message=f"Bucket '{bucket_name}' already exists",
                suggestion="Use a different name or delete the existing bucket first",
            )

        # Create bucket
        self._mc_command(["mb", f"{MC_ALIAS}/{bucket_name}"])

        # Generate access credentials
        access_key = f"{bucket_name}_{secrets.token_hex(8)}"
        secret_key = secrets.token_urlsafe(32)

        # Create MinIO user with bucket-specific access
        self._mc_command(["admin", "user", "add", MC_ALIAS, access_key, secret_key])

        # Create policy for this bucket
        policy_name = f"{bucket_name}-policy"
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetBucketLocation",
                        "s3:ListBucket",
                        "s3:ListBucketMultipartUploads",
                    ],
                    "Resource": [f"arn:aws:s3:::{bucket_name}"],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListMultipartUploadParts",
                        "s3:AbortMultipartUpload",
                    ],
                    "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
                },
            ],
        }

        # Write policy to temp file and apply
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(policy, f)
            policy_file = f.name

        try:
            self._mc_command(["admin", "policy", "create", MC_ALIAS, policy_name, policy_file])
        finally:
            Path(policy_file).unlink(missing_ok=True)

        # Attach policy to user
        self._mc_command(["admin", "policy", "attach", MC_ALIAS, policy_name, "--user", access_key])

        # Update storage config
        config = self._get_storage_config()
        if "buckets" not in config:
            config["buckets"] = {}
        config["buckets"][bucket_name] = project
        if "credentials" not in config:
            config["credentials"] = {}
        config["credentials"][bucket_name] = {
            "access_key": access_key,
            "policy": policy_name,
        }
        self._save_storage_config(config)

        credentials = S3Credentials(
            endpoint=self._get_minio_endpoint(),
            bucket=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
        )

        # Update project .env if specified
        if project:
            self._update_project_env(project, credentials)

        return credentials

    def delete_bucket(self, bucket_name: str, force: bool = False) -> None:
        """Delete a storage bucket.

        Args:
            bucket_name: Name of the bucket to delete.
            force: Must be True to confirm deletion.
        """
        if not force:
            raise StorageServiceError(
                code="FORCE_REQUIRED",
                message="Deleting a bucket requires --force flag",
                suggestion="Add --force to confirm deletion",
            )

        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion="Start MinIO with 'systemctl start minio'",
            )

        self._ensure_mc_alias()

        # Check bucket exists
        result = self._mc_command(["ls", f"{MC_ALIAS}/{bucket_name}"], check=False)
        if result.returncode != 0:
            raise StorageServiceError(
                code="BUCKET_NOT_FOUND",
                message=f"Bucket '{bucket_name}' does not exist",
            )

        # Remove all objects first (required for deletion)
        self._mc_command(["rm", "--recursive", "--force", f"{MC_ALIAS}/{bucket_name}"], check=False)

        # Remove bucket
        self._mc_command(["rb", f"{MC_ALIAS}/{bucket_name}"])

        # Get credentials info before deleting config
        config = self._get_storage_config()
        cred_info = config.get("credentials", {}).get(bucket_name, {})

        # Remove user and policy
        if cred_info:
            access_key = cred_info.get("access_key")
            policy_name = cred_info.get("policy")

            if policy_name:
                self._mc_command(
                    ["admin", "policy", "detach", MC_ALIAS, policy_name, "--user", access_key],
                    check=False,
                )
                self._mc_command(["admin", "policy", "remove", MC_ALIAS, policy_name], check=False)

            if access_key:
                self._mc_command(["admin", "user", "remove", MC_ALIAS, access_key], check=False)

        # Update storage config
        if bucket_name in config.get("buckets", {}):
            del config["buckets"][bucket_name]
        if bucket_name in config.get("credentials", {}):
            del config["credentials"][bucket_name]
        self._save_storage_config(config)

    def get_credentials(self, project: str, regenerate: bool = False) -> S3Credentials:
        """Get or regenerate S3 credentials for a project.

        Args:
            project: Project name.
            regenerate: If True, regenerate the secret key.

        Returns:
            S3 credentials for the project's bucket.
        """
        config = self._get_storage_config()

        # Find bucket for project
        bucket_name = None
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                bucket_name = bucket
                break

        if not bucket_name:
            raise StorageServiceError(
                code="NO_BUCKET_FOR_PROJECT",
                message=f"No storage bucket found for project '{project}'",
                suggestion=(
                    f"Create one with 'hostkit storage create-bucket {project}-storage {project}'"
                ),
            )

        cred_info = config.get("credentials", {}).get(bucket_name, {})
        if not cred_info:
            raise StorageServiceError(
                code="CREDENTIALS_NOT_FOUND",
                message=f"Credentials not found for bucket '{bucket_name}'",
                suggestion="The bucket may have been created manually",
            )

        access_key = cred_info["access_key"]

        if regenerate:
            # Generate new secret key
            self._ensure_mc_alias()
            secret_key = secrets.token_urlsafe(32)

            # Update user password (secret key)
            # mc admin user password sets a new secret key
            self._mc_command(
                [
                    "admin",
                    "user",
                    "add",
                    MC_ALIAS,
                    access_key,
                    secret_key,
                ]
            )
        else:
            # Can't retrieve existing secret key, so read from project .env
            env_path = Path(f"/home/{project}/.env")
            secret_key = None

            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("S3_SECRET_KEY="):
                        secret_key = line.split("=", 1)[1].strip()
                        break

            if not secret_key:
                raise StorageServiceError(
                    code="SECRET_KEY_NOT_FOUND",
                    message="Cannot retrieve secret key",
                    suggestion="Use --regenerate to create a new secret key",
                )

        credentials = S3Credentials(
            endpoint=self._get_minio_endpoint(),
            bucket=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
        )

        if regenerate:
            self._update_project_env(project, credentials)

        return credentials

    def get_usage(self) -> dict[str, Any]:
        """Get storage usage statistics."""
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion="Start MinIO with 'systemctl start minio'",
            )

        self._ensure_mc_alias()

        buckets = self.list_buckets()

        # Parse sizes to bytes for summing
        def parse_size(size_str: str) -> int:
            """Convert size string to bytes."""
            size_str = size_str.strip().upper()
            multipliers = {
                "B": 1,
                "KIB": 1024,
                "MIB": 1024**2,
                "GIB": 1024**3,
                "TIB": 1024**4,
                "KB": 1000,
                "MB": 1000**2,
                "GB": 1000**3,
                "TB": 1000**4,
            }
            for suffix, mult in multipliers.items():
                if size_str.endswith(suffix):
                    try:
                        return int(float(size_str[: -len(suffix)].strip()) * mult)
                    except ValueError:
                        return 0
            try:
                return int(float(size_str))
            except ValueError:
                return 0

        total_bytes = sum(parse_size(b.size) for b in buckets)
        total_objects = sum(b.objects for b in buckets)

        # Get disk usage
        disk_usage = "N/A"
        try:
            result = subprocess.run(
                ["df", "-h", MINIO_DATA_DIR],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) > 1:
                    parts = lines[1].split()
                    if len(parts) >= 4:
                        disk_usage = f"{parts[2]} used / {parts[1]} total ({parts[4]})"
        except (subprocess.SubprocessError, IndexError):
            pass

        return {
            "bucket_count": len(buckets),
            "total_objects": total_objects,
            "total_bytes": total_bytes,
            "total_size": self._format_size(total_bytes),
            "disk_usage": disk_usage,
            "buckets": [
                {
                    "name": b.name,
                    "project": b.project,
                    "size": b.size,
                    "objects": b.objects,
                }
                for b in buckets
            ],
        }

    def _format_size(self, bytes_val: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"

    def _update_project_env(self, project: str, credentials: S3Credentials) -> None:
        """Update project's .env file with S3 credentials."""
        env_path = Path(f"/home/{project}/.env")

        if not env_path.exists():
            raise StorageServiceError(
                code="ENV_FILE_NOT_FOUND",
                message=f"Project .env file not found: {env_path}",
                suggestion=f"Ensure project '{project}' exists",
            )

        # Read existing content
        content = env_path.read_text()

        # Remove existing S3 lines
        lines = []
        skip_s3_block = False
        for line in content.splitlines():
            if line.strip() == "# MinIO S3 Storage":
                skip_s3_block = True
                continue
            if skip_s3_block and (line.startswith("S3_") or line.startswith("AWS_")):
                continue
            skip_s3_block = False
            lines.append(line)

        # Append new S3 config
        new_content = "\n".join(lines).rstrip() + "\n\n" + credentials.to_env_format()
        env_path.write_text(new_content)

    def cleanup_project_bucket(self, project: str) -> bool:
        """Clean up storage bucket when a project is deleted.

        Args:
            project: Project name.

        Returns:
            True if bucket was deleted, False if no bucket found.
        """
        config = self._get_storage_config()

        # Find bucket for project
        bucket_name = None
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                bucket_name = bucket
                break

        if bucket_name:
            try:
                self.delete_bucket(bucket_name, force=True)
                return True
            except StorageServiceError:
                pass

        return False

    def get_bucket_policy(self, bucket_name: str) -> dict[str, Any]:
        """Get the current access policy for a bucket.

        Args:
            bucket_name: Name of the bucket.

        Returns:
            Policy information dict.
        """
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion="Start MinIO with 'systemctl start minio'",
            )

        self._ensure_mc_alias()

        # Check bucket exists
        result = self._mc_command(["ls", f"{MC_ALIAS}/{bucket_name}"], check=False)
        if result.returncode != 0:
            raise StorageServiceError(
                code="BUCKET_NOT_FOUND",
                message=f"Bucket '{bucket_name}' does not exist",
                suggestion="Run 'hostkit storage list' to see available buckets",
            )

        # Get anonymous policy
        result = self._mc_command(["anonymous", "get", f"{MC_ALIAS}/{bucket_name}"], check=False)

        policy = "private"
        if result.returncode == 0:
            output = result.stdout.strip().lower()
            if "download" in output:
                policy = "public-read"
            elif "upload" in output:
                policy = "public-write"
            elif "public" in output:
                policy = "public-read-write"

        # Get project association
        config = self._get_storage_config()
        project = config.get("buckets", {}).get(bucket_name)

        # Build public URL using external endpoint
        public_url = None
        if policy != "private":
            public_endpoint = self._get_minio_endpoint(public=True)
            if public_endpoint:
                public_url = f"{public_endpoint}/{bucket_name}"
            else:
                # Fall back to internal URL with note
                internal_url = self._get_minio_endpoint()
                public_url = (
                    f"{internal_url}/{bucket_name}"
                    " (internal only - run"
                    " 'hostkit storage proxy <domain>'"
                    " to expose externally)"
                )

        return {
            "bucket": bucket_name,
            "project": project,
            "policy": policy,
            "public_url": public_url,
        }

    def set_bucket_policy(
        self,
        bucket_name: str,
        policy: str,
        prefix: str | None = None,
    ) -> dict[str, Any]:
        """Set the access policy for a bucket or prefix.

        Args:
            bucket_name: Name of the bucket.
            policy: Policy to set: 'private', 'public-read', 'public-write', 'public-read-write'.
            prefix: Optional path prefix to apply policy to (e.g., 'uploads/').

        Returns:
            Result dict with policy info.
        """
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion="Start MinIO with 'systemctl start minio'",
            )

        # Validate policy
        valid_policies = ["private", "public-read", "public-write", "public-read-write"]
        if policy not in valid_policies:
            raise StorageServiceError(
                code="INVALID_POLICY",
                message=f"Invalid policy: {policy}",
                suggestion=f"Valid policies: {', '.join(valid_policies)}",
            )

        self._ensure_mc_alias()

        # Check bucket exists
        result = self._mc_command(["ls", f"{MC_ALIAS}/{bucket_name}"], check=False)
        if result.returncode != 0:
            raise StorageServiceError(
                code="BUCKET_NOT_FOUND",
                message=f"Bucket '{bucket_name}' does not exist",
                suggestion="Run 'hostkit storage list' to see available buckets",
            )

        # Build target path
        target = f"{MC_ALIAS}/{bucket_name}"
        if prefix:
            # Normalize prefix (ensure it ends with / for directories)
            prefix = prefix.rstrip("/") + "/"
            target = f"{target}/{prefix}"

        # Map policy to mc anonymous command
        policy_map = {
            "private": "none",
            "public-read": "download",
            "public-write": "upload",
            "public-read-write": "public",
        }
        mc_policy = policy_map[policy]

        # Set the policy
        self._mc_command(["anonymous", "set", mc_policy, target])

        # Build public URL using external endpoint
        public_url = None
        proxy_note = None
        if policy != "private":
            public_endpoint = self._get_minio_endpoint(public=True)
            if public_endpoint:
                if prefix:
                    public_url = f"{public_endpoint}/{bucket_name}/{prefix}"
                else:
                    public_url = f"{public_endpoint}/{bucket_name}"
            else:
                # No proxy configured
                proxy_note = (
                    "No external proxy configured."
                    " Run 'hostkit storage proxy <domain>'"
                    " to expose MinIO externally."
                )
                if prefix:
                    public_url = f"http://localhost:9000/{bucket_name}/{prefix}"
                else:
                    public_url = f"http://localhost:9000/{bucket_name}"

        # Get project association
        config = self._get_storage_config()
        project = config.get("buckets", {}).get(bucket_name)

        result = {
            "bucket": bucket_name,
            "project": project,
            "policy": policy,
            "prefix": prefix,
            "public_url": public_url,
        }
        if proxy_note:
            result["note"] = proxy_note

        return result

    def storage_is_enabled(self, project: str) -> bool:
        """Check if MinIO storage is enabled for a project.

        Args:
            project: Project name.

        Returns:
            True if storage is enabled (project has a bucket).
        """
        config = self._get_storage_config()
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                return True
        return False

    def enable_for_project(
        self,
        project: str,
        public: bool = False,
    ) -> dict[str, Any]:
        """Enable MinIO storage for a project.

        Creates a bucket named 'hostkit-{project}' with isolated credentials.
        This follows the standard HostKit service enable pattern.

        Args:
            project: Project name.
            public: If True, make the bucket publicly readable.

        Returns:
            Dict with bucket info and credentials.
        """
        if not self.is_minio_running():
            raise StorageServiceError(
                code="MINIO_NOT_RUNNING",
                message="MinIO service is not running",
                suggestion="Run 'hostkit storage setup' to configure MinIO",
            )

        # Check project exists
        proj = self.db.get_project(project)
        if not proj:
            raise StorageServiceError(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )

        # Standard bucket naming: hostkit-{project}
        bucket_name = f"hostkit-{project}"

        # Check if already enabled
        config = self._get_storage_config()
        for existing_bucket, proj_name in config.get("buckets", {}).items():
            if proj_name == project:
                raise StorageServiceError(
                    code="STORAGE_ALREADY_ENABLED",
                    message=f"Storage already enabled for '{project}' (bucket: {existing_bucket})",
                    suggestion=f"Use 'hostkit storage credentials {project}' to view credentials",
                )

        # Create bucket with credentials
        credentials = self.create_bucket(bucket_name, project)

        # Set public policy if requested
        if public:
            self.set_bucket_policy(bucket_name, "public-read")

        # Add public URL to credentials
        public_endpoint = self._get_minio_endpoint(public=True)
        if public_endpoint:
            credentials.public_url = f"{public_endpoint}/{bucket_name}"

        # Update project env with public URL
        if credentials.public_url:
            self._update_project_env(project, credentials)

        return {
            "project": project,
            "bucket": bucket_name,
            "endpoint": credentials.endpoint,
            "public_url": credentials.public_url,
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "public": public,
            "env_updated": True,
        }

    def disable_for_project(self, project: str, force: bool = False) -> dict[str, Any]:
        """Disable MinIO storage for a project.

        Deletes the project's bucket and revokes credentials.

        Args:
            project: Project name.
            force: Must be True to confirm deletion.

        Returns:
            Dict with deletion result.
        """
        if not force:
            raise StorageServiceError(
                code="FORCE_REQUIRED",
                message="Disabling storage requires --force flag",
                suggestion="Add --force to confirm deletion of bucket and all data",
            )

        # Find bucket for project
        config = self._get_storage_config()
        bucket_name = None
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                bucket_name = bucket
                break

        if not bucket_name:
            raise StorageServiceError(
                code="STORAGE_NOT_ENABLED",
                message=f"Storage not enabled for '{project}'",
                suggestion="Use 'hostkit storage enable {project}' to enable storage",
            )

        # Delete the bucket
        self.delete_bucket(bucket_name, force=True)

        # Remove S3 env vars from project
        self._remove_project_env(project)

        return {
            "project": project,
            "bucket": bucket_name,
            "deleted": True,
            "credentials_revoked": True,
        }

    def _remove_project_env(self, project: str) -> None:
        """Remove S3 environment variables from project's .env file."""
        env_path = Path(f"/home/{project}/.env")

        if not env_path.exists():
            return

        # Read existing content
        content = env_path.read_text()

        # Remove S3-related lines
        lines = []
        skip_s3_block = False
        s3_vars = {
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
            "S3_REGION",
            "S3_PUBLIC_URL",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        }

        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "# MinIO S3 Storage":
                skip_s3_block = True
                continue
            if skip_s3_block:
                var_name = stripped.split("=")[0] if "=" in stripped else ""
                if var_name in s3_vars or stripped == "":
                    continue
                skip_s3_block = False
            lines.append(line)

        new_content = "\n".join(lines).rstrip() + "\n"
        env_path.write_text(new_content)

    def get_project_storage_status(self, project: str) -> dict[str, Any]:
        """Get storage status for a specific project.

        Args:
            project: Project name.

        Returns:
            Dict with storage status for the project.
        """
        config = self._get_storage_config()

        # Find bucket for project
        bucket_name = None
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                bucket_name = bucket
                break

        if not bucket_name:
            return {
                "enabled": False,
                "project": project,
                "bucket": None,
            }

        # Get bucket info
        try:
            bucket_info = self._get_bucket_info(bucket_name)
            policy_info = self.get_bucket_policy(bucket_name)
        except StorageServiceError:
            return {
                "enabled": True,
                "project": project,
                "bucket": bucket_name,
                "error": "Could not retrieve bucket info",
            }

        public_endpoint = self._get_minio_endpoint(public=True)
        public_url = f"{public_endpoint}/{bucket_name}" if public_endpoint else None

        return {
            "enabled": True,
            "project": project,
            "bucket": bucket_name,
            "size": bucket_info.size,
            "objects": bucket_info.objects,
            "policy": policy_info.get("policy", "private"),
            "public_url": public_url if policy_info.get("policy") != "private" else None,
            "endpoint": self._get_minio_endpoint(),
        }

    def upload_file(
        self,
        project: str,
        local_path: str,
        object_key: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to a project's bucket.

        Args:
            project: Project name (must have storage enabled).
            local_path: Absolute path to file on VPS.
            object_key: Object key/path in bucket. Default: uploads/<timestamp>-<filename>.
            content_type: MIME type override. Auto-detected if omitted.

        Returns:
            Dict with url, bucket, object_key, size_bytes, content_type.

        Raises:
            StorageServiceError: If validation fails or upload fails.
        """
        # Validate file exists and is a file
        local_file = Path(local_path)
        if not local_file.exists():
            raise StorageServiceError(
                code="FILE_NOT_FOUND",
                message=f"File not found: {local_path}",
                suggestion="Verify the file path is correct",
            )

        if not local_file.is_file():
            raise StorageServiceError(
                code="NOT_A_FILE",
                message=f"Path is not a file: {local_path}",
                suggestion="Ensure the path points to a file, not a directory",
            )

        # Get project bucket
        config = self._get_storage_config()
        bucket_name = None
        for bucket, proj in config.get("buckets", {}).items():
            if proj == project:
                bucket_name = bucket
                break

        if not bucket_name:
            raise StorageServiceError(
                code="STORAGE_NOT_ENABLED",
                message=f"Storage not enabled for project '{project}'",
                suggestion=f"Enable storage with: hostkit storage enable {project}",
            )

        # Get credentials for the bucket
        cred_info = config.get("credentials", {}).get(bucket_name, {})
        if not cred_info:
            raise StorageServiceError(
                code="CREDENTIALS_NOT_FOUND",
                message=f"Credentials not found for bucket '{bucket_name}'",
                suggestion="The bucket may have been created manually",
            )

        # Ensure mc alias is configured
        self._ensure_mc_alias()

        # Generate object key if not provided
        if not object_key:
            import time
            timestamp = int(time.time())
            filename = local_file.name
            object_key = f"uploads/{timestamp}-{filename}"

        # Get file size
        file_size = local_file.stat().st_size

        # Build mc cp command
        target = f"{MC_ALIAS}/{bucket_name}/{object_key}"

        # Run upload command
        cmd = ["cp"]
        if content_type:
            cmd.extend(["--attr", f"Content-Type={content_type}"])
        cmd.extend([str(local_file), target])

        result = self._mc_command(cmd, check=False)

        if result.returncode != 0:
            raise StorageServiceError(
                code="UPLOAD_FAILED",
                message=f"Upload failed: {result.stderr or result.stdout}",
                suggestion="Check file permissions and bucket access",
            )

        # Build URL response
        public_endpoint = self._get_minio_endpoint(public=True)
        internal_endpoint = self._get_minio_endpoint(public=False)

        # Determine which URL to use
        if public_endpoint:
            url = f"{public_endpoint}/{bucket_name}/{object_key}"
            public_url = url
        else:
            url = f"{internal_endpoint}/{bucket_name}/{object_key}"
            public_url = url

        return {
            "url": url,
            "public_url": public_url,
            "bucket": bucket_name,
            "object_key": object_key,
            "size_bytes": file_size,
            "content_type": content_type or "application/octet-stream",
        }

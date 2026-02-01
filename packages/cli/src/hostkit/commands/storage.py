"""Object storage (MinIO) management commands for HostKit."""

import click

from hostkit.output import OutputFormatter
from hostkit.services.storage_service import StorageService, StorageServiceError


@click.group()
@click.pass_context
def storage(ctx: click.Context) -> None:
    """Manage MinIO object storage for projects.

    MinIO provides S3-compatible object storage. Each project can have
    its own bucket with isolated access credentials.
    """
    pass


@storage.command("enable")
@click.argument("project")
@click.option("--public", is_flag=True, help="Make bucket publicly readable")
@click.pass_context
def storage_enable(ctx: click.Context, project: str, public: bool) -> None:
    """Enable MinIO storage for a project.

    Creates a bucket named 'hostkit-{project}' with isolated S3 credentials.
    Credentials are automatically added to the project's .env file.

    \b
    Examples:
        hostkit storage enable myapp              # Private bucket
        hostkit storage enable myapp --public     # Public-read bucket
        hostkit minio enable myapp                # Alias for storage enable
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        result = service.enable_for_project(project, public=public)

        formatter.success(
            message=f"Storage enabled for '{project}'",
            data=result,
        )

        if not ctx.obj["json_mode"]:
            click.echo(f"\nS3 Credentials for '{project}':")
            click.echo("-" * 50)
            click.echo(f"  Endpoint:    {result['endpoint']}")
            click.echo(f"  Bucket:      {result['bucket']}")
            click.echo(f"  Access Key:  {result['access_key']}")
            click.echo(f"  Secret Key:  {result['secret_key']}")
            if result.get("public_url"):
                click.echo(f"  Public URL:  {result['public_url']}")
            click.echo("\n  Credentials added to project .env")

            if public:
                click.echo("\n  Public files accessible at:")
                click.echo(f"    {result['public_url']}/<filename>")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion of bucket and data")
@click.pass_context
def storage_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable MinIO storage for a project.

    Deletes the project's bucket, all stored data, and revokes credentials.
    Requires --force to confirm.

    \b
    Examples:
        hostkit storage disable myapp --force
        hostkit minio disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        result = service.disable_for_project(project, force=force)

        formatter.success(
            message=f"Storage disabled for '{project}'",
            data=result,
        )

        if not ctx.obj["json_mode"]:
            click.echo(f"\n  Bucket '{result['bucket']}' deleted")
            click.echo("  Credentials revoked")
            click.echo("  Environment variables removed")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("status")
@click.pass_context
def storage_status(ctx: click.Context) -> None:
    """Show MinIO service status.

    Displays whether MinIO is installed, running, and connection details.

    Example:
        hostkit storage status
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        status = service.get_status()

        if ctx.obj["json_mode"]:
            formatter.success(
                message="MinIO status",
                data=status,
            )
        else:
            click.echo("\nMinIO Object Storage Status")
            click.echo("-" * 40)
            click.echo(f"  Installed: {'Yes' if status['installed'] else 'No'}")
            click.echo(f"  Running:   {'Yes' if status['running'] else 'No'}")

            if status["running"]:
                click.echo(f"  Endpoint:  {status['endpoint']}")
                click.echo(f"  Console:   {status['console_url']}")
                if "bucket_count" in status:
                    click.echo(f"  Buckets:   {status['bucket_count']}")
            else:
                click.echo("\n  Run 'hostkit storage setup' to configure MinIO")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("setup")
@click.option("--root-password", help="Custom root password (auto-generated if not provided)")
@click.pass_context
def storage_setup(ctx: click.Context, root_password: str | None) -> None:
    """Install and configure MinIO object storage.

    Downloads MinIO binary, creates system user, configures systemd service,
    and starts MinIO. Root credentials are generated or can be specified.

    Example:
        hostkit storage setup
        hostkit storage setup --root-password mysecretpassword
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    if not ctx.obj["json_mode"]:
        click.echo("Setting up MinIO object storage...")

    try:
        result = service.setup(root_password=root_password)

        formatter.success(
            message="MinIO installed and configured",
            data={
                "installed": result["installed"],
                "running": result["running"],
                "endpoint": result["endpoint"],
                "console_url": result["console_url"],
                "root_user": result["root_user"],
                "root_password": result["root_password"],
                "data_dir": result["data_dir"],
            },
        )

        if not ctx.obj["json_mode"]:
            click.echo("\nIMPORTANT: Save these root credentials securely!")
            click.echo(f"  Root User:     {result['root_user']}")
            click.echo(f"  Root Password: {result['root_password']}")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("list")
@click.pass_context
def storage_list(ctx: click.Context) -> None:
    """List all storage buckets.

    Shows bucket name, associated project, size, and object count.

    Example:
        hostkit storage list
        hostkit --json storage list
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        buckets = service.list_buckets()

        if not buckets:
            formatter.success(
                message="No buckets found",
                data={"buckets": [], "count": 0},
            )
            return

        data = {
            "buckets": [
                {
                    "name": b.name,
                    "project": b.project,
                    "size": b.size,
                    "objects": b.objects,
                }
                for b in buckets
            ],
            "count": len(buckets),
        }

        if ctx.obj["json_mode"]:
            formatter.success(message=f"Found {len(buckets)} bucket(s)", data=data)
        else:
            click.echo("\nStorage Buckets:")
            click.echo("-" * 70)
            click.echo(f"{'BUCKET':<25} {'PROJECT':<15} {'SIZE':<12} {'OBJECTS':<10}")
            click.echo("-" * 70)

            for b in buckets:
                project = b.project or "-"
                click.echo(f"{b.name:<25} {project:<15} {b.size:<12} {b.objects:<10}")

            click.echo("-" * 70)
            click.echo(f"Total: {len(buckets)} bucket(s)")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("create-bucket")
@click.argument("bucket_name")
@click.argument("project", required=False)
@click.pass_context
def storage_create_bucket(ctx: click.Context, bucket_name: str, project: str | None) -> None:
    """Create a new storage bucket with access credentials.

    Creates an S3-compatible bucket with isolated access credentials.
    If a project is specified, S3 credentials are added to the project's .env.

    Example:
        hostkit storage create-bucket myapp-files myapp
        hostkit storage create-bucket shared-assets
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    # Verify project exists if specified
    if project:
        from hostkit.database import get_db

        db = get_db()
        if not db.get_project(project):
            formatter.error(
                code="PROJECT_NOT_FOUND",
                message=f"Project '{project}' does not exist",
                suggestion="Create the project first with 'hostkit project create'",
            )
            raise SystemExit(1)

    try:
        credentials = service.create_bucket(bucket_name, project)

        data = {
            "bucket": credentials.bucket,
            "project": project,
            "endpoint": credentials.endpoint,
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "region": credentials.region,
            "env_updated": project is not None,
        }

        formatter.success(
            message=f"Bucket '{bucket_name}' created",
            data=data,
        )

        if not ctx.obj["json_mode"]:
            click.echo("\nS3 Credentials:")
            click.echo("-" * 50)
            click.echo(credentials.to_env_format())

            if not project:
                click.echo("\nTo add to a project's .env:")
                click.echo(f"  hostkit storage credentials {bucket_name}")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("delete-bucket")
@click.argument("bucket_name")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
def storage_delete_bucket(ctx: click.Context, bucket_name: str, force: bool) -> None:
    """Delete a storage bucket and its contents.

    Deletes all objects in the bucket, removes the bucket, and revokes
    access credentials. Requires --force to confirm.

    Example:
        hostkit storage delete-bucket myapp-files --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        service.delete_bucket(bucket_name, force=force)

        formatter.success(
            message=f"Bucket '{bucket_name}' deleted",
            data={
                "bucket": bucket_name,
                "deleted": True,
                "credentials_revoked": True,
            },
        )

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("credentials")
@click.argument("project")
@click.option("--regenerate", is_flag=True, help="Generate new secret key")
@click.option("--env-format", is_flag=True, help="Output in .env format only")
@click.pass_context
def storage_credentials(
    ctx: click.Context,
    project: str,
    regenerate: bool,
    env_format: bool,
) -> None:
    """Get or regenerate S3 credentials for a project.

    Shows the S3 endpoint, bucket name, and access credentials.
    Use --regenerate to create a new secret key.

    Example:
        hostkit storage credentials myapp
        hostkit storage credentials myapp --regenerate
        hostkit storage credentials myapp --env-format
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        credentials = service.get_credentials(project, regenerate=regenerate)

        if env_format:
            click.echo(credentials.to_env_format())
            return

        data = {
            "project": project,
            "bucket": credentials.bucket,
            "endpoint": credentials.endpoint,
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "region": credentials.region,
            "regenerated": regenerate,
        }

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"S3 credentials for '{project}'",
                data=data,
            )
        else:
            click.echo(f"\nS3 Credentials for '{project}'")
            click.echo("-" * 50)
            click.echo(f"  Endpoint:    {credentials.endpoint}")
            click.echo(f"  Bucket:      {credentials.bucket}")
            click.echo(f"  Access Key:  {credentials.access_key}")
            click.echo(f"  Secret Key:  {credentials.secret_key}")
            click.echo(f"  Region:      {credentials.region}")

            if regenerate:
                click.echo("\n  (Secret key was regenerated and .env updated)")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("usage")
@click.pass_context
def storage_usage(ctx: click.Context) -> None:
    """Show storage usage statistics.

    Displays total storage used, object counts, and per-bucket breakdown.

    Example:
        hostkit storage usage
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        usage = service.get_usage()

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Storage usage",
                data=usage,
            )
        else:
            click.echo("\nStorage Usage")
            click.echo("-" * 60)
            click.echo(f"  Total Buckets:  {usage['bucket_count']}")
            click.echo(f"  Total Objects:  {usage['total_objects']}")
            click.echo(f"  Total Size:     {usage['total_size']}")
            click.echo(f"  Disk Usage:     {usage['disk_usage']}")

            if usage["buckets"]:
                click.echo("\nPer-Bucket Usage:")
                click.echo("-" * 60)
                click.echo(f"{'BUCKET':<25} {'PROJECT':<15} {'SIZE':<12} {'OBJECTS':<10}")
                click.echo("-" * 60)

                for b in usage["buckets"]:
                    project = b["project"] or "-"
                    click.echo(f"{b['name']:<25} {project:<15} {b['size']:<12} {b['objects']:<10}")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("policy")
@click.argument("bucket_name")
@click.argument("policy", required=False)
@click.option("--prefix", help="Apply policy to specific path prefix (e.g., 'uploads/')")
@click.option("--show", is_flag=True, help="Show current policy instead of setting")
@click.pass_context
def storage_policy(
    ctx: click.Context,
    bucket_name: str,
    policy: str | None,
    prefix: str | None,
    show: bool,
) -> None:
    """Get or set bucket access policy.

    Controls public access to bucket contents. By default buckets are private.

    \b
    Policies:
      private          - No public access (default)
      public-read      - Anyone can download/view files
      public-write     - Anyone can upload files (use with caution)
      public-read-write - Full public access (use with caution)

    \b
    Examples:
        hostkit storage policy myapp-images --show
        hostkit storage policy myapp-images public-read
        hostkit storage policy myapp-images public-read --prefix uploads/
        hostkit storage policy myapp-images private
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    try:
        if show or not policy:
            # Show current policy
            result = service.get_bucket_policy(bucket_name)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Policy for '{bucket_name}'",
                    data=result,
                )
            else:
                click.echo(f"\nBucket Policy: {bucket_name}")
                click.echo("-" * 40)
                click.echo(f"  Policy:  {result['policy']}")
                if result["project"]:
                    click.echo(f"  Project: {result['project']}")
                if result["public_url"]:
                    click.echo(f"  URL:     {result['public_url']}")
                else:
                    click.echo("  URL:     (not publicly accessible)")
        else:
            # Set policy
            result = service.set_bucket_policy(bucket_name, policy, prefix)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"Policy set to '{policy}' for '{bucket_name}'",
                    data=result,
                )
            else:
                click.echo(f"\n✓ Policy updated for '{bucket_name}'")
                click.echo("-" * 40)
                click.echo(f"  Policy:  {result['policy']}")
                if result["prefix"]:
                    click.echo(f"  Prefix:  {result['prefix']}")
                if result["public_url"]:
                    click.echo(f"  URL:     {result['public_url']}")

                if policy == "public-read":
                    click.echo("\n  Files are now publicly readable at:")
                    click.echo(f"    {result['public_url']}/<filename>")

                if result.get("note"):
                    click.echo(f"\n  Note: {result['note']}")

    except StorageServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@storage.command("proxy")
@click.argument("domain")
@click.option("--ssl", is_flag=True, help="Enable SSL (requires certificate)")
@click.pass_context
def storage_proxy(ctx: click.Context, domain: str, ssl: bool) -> None:
    """Configure Nginx proxy for external S3 API access.

    Creates an Nginx configuration to proxy S3 requests through a domain,
    enabling external access to the MinIO API with a custom domain.

    Example:
        hostkit storage proxy s3.example.com
        hostkit storage proxy s3.example.com --ssl
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = StorageService()

    if not service.is_minio_running():
        formatter.error(
            code="MINIO_NOT_RUNNING",
            message="MinIO service is not running",
            suggestion="Start MinIO with 'hostkit storage setup'",
        )
        raise SystemExit(1)

    try:
        import subprocess
        from pathlib import Path

        # Create Nginx config for MinIO proxy
        if ssl:
            config = f"""# MinIO S3 API Proxy - Managed by HostKit
# Domain: {domain}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # Allow large uploads
    client_max_body_size 0;

    # Proxy to MinIO
    location / {{
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # S3-specific headers
        proxy_set_header X-Amz-Content-Sha256 $http_x_amz_content_sha256;

        # Buffering off for uploads
        proxy_buffering off;
        proxy_request_buffering off;
    }}
}}

server {{
    listen 80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}
"""
        else:
            config = f"""# MinIO S3 API Proxy - Managed by HostKit
# Domain: {domain}

server {{
    listen 80;
    server_name {domain};

    # Allow large uploads
    client_max_body_size 0;

    # Proxy to MinIO
    location / {{
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # S3-specific headers
        proxy_set_header X-Amz-Content-Sha256 $http_x_amz_content_sha256;

        # Buffering off for uploads
        proxy_buffering off;
        proxy_request_buffering off;
    }}
}}
"""

        config_path = Path("/etc/nginx/sites-available/minio-proxy")
        config_path.write_text(config)

        enabled_path = Path("/etc/nginx/sites-enabled/minio-proxy")
        if not enabled_path.exists():
            enabled_path.symlink_to(config_path)

        # Test and reload Nginx
        subprocess.run(["nginx", "-t"], check=True, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)

        formatter.success(
            message=f"MinIO proxy configured for {domain}",
            data={
                "domain": domain,
                "ssl": ssl,
                "endpoint": f"{'https' if ssl else 'http'}://{domain}",
                "config_path": str(config_path),
            },
        )

        if not ctx.obj["json_mode"]:
            click.echo(f"\nS3 API is now accessible at: {'https' if ssl else 'http'}://{domain}")
            if not ssl:
                click.echo("\nTo enable SSL, first provision a certificate:")
                click.echo(f"  hostkit ssl provision {domain}")
                click.echo(f"  hostkit storage proxy {domain} --ssl")

    except subprocess.CalledProcessError as e:
        formatter.error(
            code="NGINX_CONFIG_FAILED",
            message=(
                f"Failed to configure Nginx: {e.stderr.decode() if e.stderr else 'unknown error'}"
            ),
            suggestion="Check Nginx configuration with 'nginx -t'",
        )
        raise SystemExit(1)
    except Exception as e:
        formatter.error(
            code="PROXY_SETUP_FAILED",
            message=f"Failed to set up proxy: {e}",
        )
        raise SystemExit(1)


# Create 'minio' as an alias for 'storage' for convenience
# Users can use either: hostkit storage enable myapp OR hostkit minio enable myapp
@click.group()
@click.pass_context
def minio(ctx: click.Context) -> None:
    """MinIO object storage for projects (alias for 'storage').

    S3-compatible object storage with per-project buckets and credentials.
    Public endpoint: https://s3.hostkit.dev

    \b
    Examples:
        hostkit minio enable myapp              # Enable storage
        hostkit minio enable myapp --public     # Enable with public access
        hostkit minio disable myapp --force     # Disable and delete bucket
        hostkit minio status                    # Show MinIO status
    """
    pass


# Register the same commands under 'minio' alias
minio.add_command(storage_enable, "enable")
minio.add_command(storage_disable, "disable")
minio.add_command(storage_status, "status")
minio.add_command(storage_list, "list")
minio.add_command(storage_credentials, "credentials")
minio.add_command(storage_usage, "usage")
minio.add_command(storage_policy, "policy")

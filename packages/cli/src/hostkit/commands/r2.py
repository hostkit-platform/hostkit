"""Cloudflare R2 object storage commands for HostKit."""

import click

from hostkit.access import project_access, project_owner
from hostkit.output import OutputFormatter
from hostkit.services.r2_service import R2Service, R2ServiceError


@click.group()
@click.pass_context
def r2(ctx: click.Context) -> None:
    """Manage Cloudflare R2 object storage for projects.

    R2 provides S3-compatible object storage with zero egress fees.
    Each project gets its own bucket: hostkit-{project}
    """
    pass


@r2.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def r2_enable(ctx: click.Context, project: str) -> None:
    """Enable R2 storage for a project.

    Creates a bucket named 'hostkit-{project}' and injects
    S3-compatible credentials into the project's .env file.

    Example:
        hostkit r2 enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.enable(project=project)

        formatter.success(
            message=f"R2 storage enabled for '{project}'",
            data={
                "project": project,
                "bucket": result["bucket"],
                "endpoint": result["endpoint"],
                "env_vars_added": [
                    "R2_ENDPOINT",
                    "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY",
                    "R2_BUCKET",
                    "AWS_ENDPOINT_URL_S3",
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_DEFAULT_REGION",
                ],
            },
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Confirm deletion")
@click.pass_context
@project_owner("project")
def r2_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable R2 storage for a project.

    Deletes all objects and the bucket. Requires --force.

    WARNING: This will delete all stored files!

    Example:
        hostkit r2 disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.disable(project=project, force=force)

        formatter.success(
            message=f"R2 storage disabled for '{project}'",
            data={
                "project": project,
                "bucket": result["bucket"],
                "deleted": True,
            },
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("status")
@click.argument("project")
@click.pass_context
@project_access("project")
def r2_status(ctx: click.Context, project: str) -> None:
    """Show R2 storage status for a project.

    Example:
        hostkit r2 status myapp
        hostkit --json r2 status myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.status(project=project)

        if result["enabled"]:
            formatter.success(
                message=f"R2 storage is enabled for '{project}'",
                data=result,
            )
        else:
            formatter.info(
                message=f"R2 storage is not enabled for '{project}'",
                data=result,
            )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("upload")
@click.argument("project")
@click.argument("local_path", type=click.Path(exists=True))
@click.argument("remote_key")
@click.option("--content-type", help="Override content type")
@click.pass_context
@project_owner("project")
def r2_upload(
    ctx: click.Context,
    project: str,
    local_path: str,
    remote_key: str,
    content_type: str | None,
) -> None:
    """Upload a file to R2 bucket.

    Example:
        hostkit r2 upload myapp ./logo.png images/logo.png
        hostkit r2 upload myapp ./data.json api/data.json --content-type application/json
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.upload(
            project=project,
            local_path=local_path,
            remote_key=remote_key,
            content_type=content_type,
        )

        formatter.success(
            message=f"Uploaded '{remote_key}' to bucket '{result['bucket']}'",
            data=result,
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("download")
@click.argument("project")
@click.argument("remote_key")
@click.argument("local_path", type=click.Path())
@click.pass_context
@project_access("project")
def r2_download(
    ctx: click.Context,
    project: str,
    remote_key: str,
    local_path: str,
) -> None:
    """Download a file from R2 bucket.

    Example:
        hostkit r2 download myapp images/logo.png ./logo.png
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.download(
            project=project,
            remote_key=remote_key,
            local_path=local_path,
        )

        formatter.success(
            message=f"Downloaded '{remote_key}' to '{local_path}'",
            data=result,
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("list")
@click.argument("project")
@click.option("--prefix", help="Filter by key prefix")
@click.option("--max-keys", default=1000, help="Maximum keys to return")
@click.pass_context
@project_access("project")
def r2_list(
    ctx: click.Context,
    project: str,
    prefix: str | None,
    max_keys: int,
) -> None:
    """List objects in project's R2 bucket.

    Example:
        hostkit r2 list myapp
        hostkit r2 list myapp --prefix images/
        hostkit --json r2 list myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        objects = service.list_objects(
            project=project,
            prefix=prefix,
            max_keys=max_keys,
        )

        # Convert to dicts for output
        objects_data = [
            {
                "key": obj.key,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat(),
                "etag": obj.etag,
            }
            for obj in objects
        ]

        formatter.success(
            message=f"Found {len(objects)} objects in bucket",
            data={
                "project": project,
                "bucket": f"hostkit-{project}",
                "count": len(objects),
                "prefix": prefix,
                "objects": objects_data,
            },
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("delete")
@click.argument("project")
@click.argument("key")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def r2_delete(
    ctx: click.Context,
    project: str,
    key: str,
    force: bool,
) -> None:
    """Delete an object from R2 bucket.

    Example:
        hostkit r2 delete myapp images/old-logo.png --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    if not force:
        formatter.error(
            code="FORCE_REQUIRED",
            message="The --force flag is required to delete an object",
            suggestion=f"Add --force to confirm: 'hostkit r2 delete {project} {key} --force'",
        )
        raise SystemExit(1)

    try:
        result = service.delete_object(project=project, key=key)

        formatter.success(
            message=f"Deleted '{key}' from bucket",
            data=result,
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("presign")
@click.argument("project")
@click.argument("key")
@click.option("--expires", default=3600, help="URL expiration in seconds (default: 3600)")
@click.option(
    "--method",
    type=click.Choice(["GET", "PUT"]),
    default="GET",
    help="HTTP method",
)
@click.pass_context
@project_access("project")
def r2_presign(
    ctx: click.Context,
    project: str,
    key: str,
    expires: int,
    method: str,
) -> None:
    """Generate a presigned URL for an object.

    GET presigned URLs allow temporary public download access.
    PUT presigned URLs allow direct uploads from clients.

    Example:
        hostkit r2 presign myapp images/logo.png
        hostkit r2 presign myapp uploads/new-file.pdf --method PUT --expires 600
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.generate_presigned_url(
            project=project,
            key=key,
            expires=expires,
            method=method,
        )

        formatter.success(
            message=f"Generated presigned URL for '{key}'",
            data=result,
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("usage")
@click.pass_context
def r2_usage(ctx: click.Context) -> None:
    """Show R2 storage usage across all projects.

    Example:
        hostkit r2 usage
        hostkit --json r2 usage
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.get_usage()

        formatter.success(
            message=f"R2 usage: {result['project_count']} projects, "
            f"{result['total_objects']} objects, {result['total_size_human']}",
            data=result,
        )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@r2.command("credentials")
@click.argument("project")
@click.option("--env-format", is_flag=True, help="Output in .env format")
@click.pass_context
@project_access("project")
def r2_credentials(
    ctx: click.Context,
    project: str,
    env_format: bool,
) -> None:
    """Show S3 credentials for a project's R2 bucket.

    Example:
        hostkit r2 credentials myapp
        hostkit r2 credentials myapp --env-format
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = R2Service()

    try:
        result = service.get_credentials(project=project, env_format=env_format)

        if env_format and "env_format" in result:
            # Print raw env format for piping
            if ctx.obj.get("json_mode"):
                formatter.success(
                    message="R2 credentials",
                    data=result,
                )
            else:
                click.echo(result["env_format"])
        else:
            formatter.success(
                message=f"R2 credentials for '{project}'",
                data=result,
            )

    except R2ServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

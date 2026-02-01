"""Vector service CLI commands for HostKit.

Provides vector embedding and semantic search services for projects.
"""

import sys

import click

from hostkit.access import project_access, project_owner, root_only
from hostkit.output import OutputFormatter
from hostkit.services.vector_service import VectorService, VectorServiceError


@click.group()
@click.pass_context
def vector(ctx: click.Context) -> None:
    """Vector embedding and semantic search service.

    Enable AI-powered semantic search for your projects with document
    ingestion, chunking, embeddings, and similarity search.
    """
    pass


# =============================================================================
# Service Management (root only)
# =============================================================================


@vector.command("setup")
@click.option("--force", is_flag=True, help="Overwrite existing setup")
@click.pass_context
@root_only
def vector_setup(ctx: click.Context, force: bool) -> None:
    """Initialize the vector service (root only).

    Creates the service database, deploys the vector service code,
    and starts the systemd services.

    Example:
        hostkit vector setup
        hostkit vector setup --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.setup(force=force)

        formatter.success(
            message="Vector service initialized",
            data={
                "service_url": result["service_url"],
                "database": result["database"],
                "service_dir": result["service_dir"],
                "log_dir": result["log_dir"],
            },
        )

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("status")
@click.pass_context
def vector_status(ctx: click.Context) -> None:
    """Show vector service status.

    Displays the health of the vector service, including database
    connection, Redis, and worker status.

    Example:
        hostkit vector status
        hostkit --json vector status
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.status()

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Vector service status",
                data=result,
            )
        else:
            click.echo("\nVector Service Status")
            click.echo("-" * 50)
            click.echo(f"  Status:   {result['status']}")
            click.echo(f"  Database: {result['database']}")
            click.echo(f"  Redis:    {result['redis']}")
            click.echo(f"  Worker:   {result['worker']}")
            click.echo(f"  Projects: {result['project_count']}")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Project Management
# =============================================================================


@vector.command("enable")
@click.argument("project")
@click.pass_context
@project_owner("project")
def vector_enable(ctx: click.Context, project: str) -> None:
    """Enable vector service for a project.

    Creates a project-specific database and generates an API key.
    The API key is shown once and should be saved securely.

    Example:
        hostkit vector enable myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.enable_project(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Vector service enabled for '{project}'",
                data=result,
            )
        else:
            click.echo()
            click.secho(f"Vector service enabled for '{project}'", fg="green", bold=True)
            click.echo()
            click.echo(f"  API Key:   {result['api_key']}")
            click.echo(f"  Database:  {result['database']}")
            click.echo(f"  Endpoint:  {result['endpoint']}")
            click.echo()
            click.secho("  Save this API key - it will not be shown again!", fg="yellow")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("disable")
@click.argument("project")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_owner("project")
def vector_disable(ctx: click.Context, project: str, force: bool) -> None:
    """Disable vector service for a project.

    Removes the project database and all vector data.
    Requires --force to confirm.

    WARNING: This will delete all embeddings and collections!

    Example:
        hostkit vector disable myapp --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    if not force:
        click.confirm(
            f"This will delete ALL vector data for '{project}'. Continue?",
            abort=True,
        )

    try:
        result = service.disable_project(project)

        formatter.success(
            message=f"Vector service disabled for '{project}'",
            data={
                "project": project,
                "database_deleted": result["database_deleted"],
                "collections_deleted": result["collections_deleted"],
                "chunks_deleted": result["chunks_deleted"],
            },
        )

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("key")
@click.argument("project")
@click.option("--regenerate", is_flag=True, help="Generate new API key")
@click.pass_context
@project_access("project")
def vector_key(ctx: click.Context, project: str, regenerate: bool) -> None:
    """Show or regenerate API key for a project.

    Without options, shows API key info (prefix, creation date).
    Use --regenerate to create a new API key (invalidates the old one).

    Example:
        hostkit vector key myapp
        hostkit vector key myapp --regenerate
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        if regenerate:
            result = service.regenerate_key(project)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message="API key regenerated",
                    data=result,
                )
            else:
                click.echo()
                click.secho("API key regenerated", fg="green", bold=True)
                click.echo()
                click.echo(f"  New API Key: {result['api_key']}")
                click.echo()
                click.secho("  Save this API key - it will not be shown again!", fg="yellow")
                click.secho("  Previous key is now invalid.", fg="yellow")
        else:
            result = service.get_key_info(project)

            if ctx.obj["json_mode"]:
                formatter.success(
                    message=f"API key info for '{project}'",
                    data=result,
                )
            else:
                click.echo(f"\nAPI Key Info: {project}")
                click.echo("-" * 50)
                click.echo(f"  Key prefix:    {result['key_prefix']}")
                click.echo(f"  Created:       {result['created_at']}")
                click.echo(f"  Last activity: {result['last_activity_at'] or 'Never'}")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Collection Management
# =============================================================================


@vector.command("collections")
@click.argument("project")
@click.pass_context
@project_access("project")
def vector_collections(ctx: click.Context, project: str) -> None:
    """List collections for a project.

    Shows all collections with document and chunk counts.

    Example:
        hostkit vector collections myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.list_collections(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Collections for '{project}'",
                data=result,
            )
        else:
            collections = result.get("collections", [])
            if not collections:
                click.echo("\nNo collections found")
            else:
                click.echo(f"\nCollections for {project}")
                click.echo("-" * 70)
                click.echo(f"{'NAME':<25} {'DOCUMENTS':<12} {'CHUNKS':<12} {'CREATED':<15}")
                click.echo("-" * 70)

                for c in collections:
                    name = c.get("name", "")
                    docs = str(c.get("document_count", 0))
                    chunks = str(c.get("chunk_count", 0))
                    created = c.get("created_at", "")[:10]
                    click.echo(f"{name:<25} {docs:<12} {chunks:<12} {created:<15}")

                click.echo("-" * 70)
                click.echo(f"Total: {len(collections)} collection(s)")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("create-collection")
@click.argument("project")
@click.argument("name")
@click.option("--description", "-d", help="Collection description")
@click.pass_context
@project_access("project")
def vector_create_collection(
    ctx: click.Context,
    project: str,
    name: str,
    description: str | None,
) -> None:
    """Create a collection.

    Collections organize documents for semantic search.

    Example:
        hostkit vector create-collection myapp docs
        hostkit vector create-collection myapp docs -d "Product documentation"
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.create_collection(project, name, description)

        formatter.success(
            message=f"Collection '{name}' created",
            data=result,
        )

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("delete-collection")
@click.argument("project")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation")
@click.pass_context
@project_access("project")
def vector_delete_collection(
    ctx: click.Context,
    project: str,
    name: str,
    force: bool,
) -> None:
    """Delete a collection.

    Removes the collection and all its documents and embeddings.
    Requires --force to confirm.

    Example:
        hostkit vector delete-collection myapp docs --force
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    if not force:
        click.confirm(f"Delete collection '{name}' and all its data?", abort=True)

    try:
        result = service.delete_collection(project, name)

        formatter.success(
            message=f"Collection '{name}' deleted",
            data=result,
        )

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("collection-info")
@click.argument("project")
@click.argument("collection")
@click.pass_context
@project_access("project")
def vector_collection_info(ctx: click.Context, project: str, collection: str) -> None:
    """Show collection details.

    Displays document count, chunk count, and metadata for a collection.

    Example:
        hostkit vector collection-info myapp docs
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.get_collection_info(project, collection)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Collection '{collection}' info",
                data=result,
            )
        else:
            click.echo(f"\nCollection: {result.get('name', collection)}")
            click.echo("-" * 50)
            click.echo(f"  Description: {result.get('description') or '-'}")
            click.echo(f"  Documents:   {result.get('document_count', 0)}")
            click.echo(f"  Chunks:      {result.get('chunk_count', 0)}")
            click.echo(f"  Created:     {result.get('created_at', '-')}")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Document Ingestion
# =============================================================================


@vector.command("ingest")
@click.argument("project")
@click.argument("collection")
@click.argument("source")
@click.option("--url", is_flag=True, help="Treat source as URL")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read content from stdin")
@click.option("--name", help="Source name for stdin input")
@click.option("--wait", is_flag=True, help="Wait for async jobs to complete")
@click.pass_context
@project_access("project")
def vector_ingest(
    ctx: click.Context,
    project: str,
    collection: str,
    source: str,
    url: bool,
    from_stdin: bool,
    name: str | None,
    wait: bool,
) -> None:
    """Ingest a document into a collection.

    SOURCE can be a file path, URL (with --url), or '-' for stdin.

    Files and URLs are processed asynchronously. Use --wait to block
    until processing completes.

    Example:
        hostkit vector ingest myapp docs ./README.md
        hostkit vector ingest myapp docs https://example.com --url
        echo "Hello world" | hostkit vector ingest myapp docs - --stdin --name hello.txt
        hostkit vector ingest myapp docs ./large.pdf --wait
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        if from_stdin or source == "-":
            # Read from stdin
            content = sys.stdin.read()
            result = service.ingest_text(
                project,
                collection,
                content,
                source_name=name or "stdin",
            )
        elif url:
            # URL ingestion
            result = service.ingest_url(project, collection, source)
        else:
            # File ingestion
            result = service.ingest_file(project, collection, source)

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Document ingested",
                data=result,
            )
        else:
            if "job_id" in result:
                click.echo()
                click.echo(f"  Job queued: {result['job_id']}")
                click.echo(f"  Status:     {result['status']}")

                if wait:
                    click.echo()
                    click.echo("  Waiting for completion...")
                    final_result = service.wait_for_job(project, result["job_id"])
                    if final_result.get("status") == "completed":
                        click.secho("  Ingestion completed!", fg="green")
                        click.echo(f"  Chunks created: {final_result.get('chunks_created', 0)}")
                    else:
                        click.secho(
                            f"  Job failed: {final_result.get('error_message', 'Unknown error')}",
                            fg="red",
                        )
            else:
                click.secho("Document ingested", fg="green")
                click.echo(f"  Chunks created: {result.get('chunks_created', 0)}")
                click.echo(f"  Tokens used:    {result.get('tokens_used', 0)}")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Search
# =============================================================================


@vector.command("search")
@click.argument("project")
@click.argument("collection")
@click.argument("query")
@click.option("--limit", "-n", default=5, help="Maximum results (default: 5)")
@click.option("--threshold", "-t", default=0.0, type=float, help="Minimum similarity (0-1)")
@click.pass_context
@project_access("project")
def vector_search(
    ctx: click.Context,
    project: str,
    collection: str,
    query: str,
    limit: int,
    threshold: float,
) -> None:
    """Search a collection.

    Performs semantic similarity search on the collection using the query.

    Example:
        hostkit vector search myapp docs "how to reset password"
        hostkit vector search myapp docs "authentication" --limit 10
        hostkit vector search myapp docs "api endpoints" --threshold 0.7
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.search(project, collection, query, limit, threshold)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Search results for '{query}'",
                data=result,
            )
        else:
            results = result.get("results", [])
            if not results:
                click.echo("\nNo results found")
            else:
                click.echo(f"\nSearch results for: {query}")
                click.echo("-" * 70)

                for i, r in enumerate(results, 1):
                    score = r.get("score", 0)
                    doc = r.get("document", {})
                    source_name = doc.get("source_name", "unknown")
                    content = r.get("content", "")[:200]

                    click.echo()
                    click.echo(f"{i}. [{score:.2f}] {source_name}")
                    click.echo(f"   {content}...")

                click.echo()
                click.echo(f"Search time: {result.get('search_time_ms', 0)}ms")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Job Management
# =============================================================================


@vector.command("jobs")
@click.argument("project")
@click.option(
    "--status",
    "status_filter",
    help="Filter by status (queued, processing, completed, failed)",
)
@click.pass_context
@project_access("project")
def vector_jobs(ctx: click.Context, project: str, status_filter: str | None) -> None:
    """List jobs for a project.

    Shows ingestion jobs and their status.

    Example:
        hostkit vector jobs myapp
        hostkit vector jobs myapp --status failed
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.list_jobs(project, status=status_filter)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Jobs for '{project}'",
                data=result,
            )
        else:
            jobs = result.get("jobs", [])
            if not jobs:
                click.echo("\nNo jobs found")
            else:
                click.echo(f"\nJobs for {project}")
                click.echo("-" * 90)
                header = (
                    f"{'ID':<25} {'COLLECTION':<15} {'SOURCE':<25} {'STATUS':<12} {'PROGRESS':<10}"
                )
                click.echo(header)
                click.echo("-" * 90)

                for j in jobs:
                    raw_id = j.get("id", "")
                    job_id = raw_id[:23] + ".." if len(raw_id) > 25 else raw_id
                    raw_coll = j.get("collection_name", "")
                    collection = raw_coll[:13] + ".." if len(raw_coll) > 15 else raw_coll
                    raw_src = j.get("source_identifier", "")
                    source = raw_src[:23] + ".." if len(raw_src) > 25 else raw_src
                    status = j.get("status", "")
                    progress = f"{j.get('progress', 0)}%"

                    click.echo(
                        f"{job_id:<25} {collection:<15} {source:<25} {status:<12} {progress:<10}"
                    )

                click.echo("-" * 90)
                click.echo(f"Total: {len(jobs)} job(s)")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@vector.command("job")
@click.argument("project")
@click.argument("job_id")
@click.pass_context
@project_access("project")
def vector_job(ctx: click.Context, project: str, job_id: str) -> None:
    """Get job details.

    Shows detailed information about a specific job.

    Example:
        hostkit vector job myapp 01HXYZ...
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.get_job(project, job_id)

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Job details",
                data=result,
            )
        else:
            click.echo(f"\nJob Details: {result.get('id', job_id)}")
            click.echo("-" * 60)
            click.echo(f"  Collection:     {result.get('collection_name', '-')}")
            click.echo(f"  Source:         {result.get('source_identifier', '-')}")
            click.echo(f"  Status:         {result.get('status', '-')}")
            click.echo(f"  Progress:       {result.get('progress', 0)}%")
            if result.get("chunks_created"):
                click.echo(f"  Chunks created: {result['chunks_created']}")
            if result.get("tokens_used"):
                click.echo(f"  Tokens used:    {result['tokens_used']}")
            if result.get("error_message"):
                click.echo()
                click.secho(f"  Error: {result['error_message']}", fg="red")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


# =============================================================================
# Usage Statistics
# =============================================================================


@vector.command("usage")
@click.argument("project")
@click.pass_context
@project_access("project")
def vector_usage(ctx: click.Context, project: str) -> None:
    """Show usage statistics for a project.

    Displays collection, document, and chunk counts, along with
    total tokens used for embeddings.

    Example:
        hostkit vector usage myapp
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = VectorService()

    try:
        result = service.get_usage(project)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Usage statistics for '{project}'",
                data=result,
            )
        else:
            storage_mb = result.get("storage_bytes", 0) / 1024 / 1024

            click.echo(f"\nUsage Statistics: {project}")
            click.echo("-" * 50)
            click.echo(f"  Collections:   {result.get('collections', 0)}")
            click.echo(f"  Documents:     {result.get('documents', 0)}")
            click.echo(f"  Chunks:        {result.get('chunks', 0)}")
            click.echo(f"  Total tokens:  {result.get('total_tokens_used', 0):,}")
            click.echo(f"  Storage:       {storage_mb:.2f} MB")

    except VectorServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

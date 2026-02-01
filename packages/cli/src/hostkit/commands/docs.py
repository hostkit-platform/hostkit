"""Documentation management commands for HostKit.

Index and manage HostKit documentation for AI agent queries.
"""

import click

from hostkit.output import OutputFormatter
from hostkit.services.docs_service import DocsService, DocsServiceError


@click.group()
@click.pass_context
def docs(ctx: click.Context) -> None:
    """Manage HostKit documentation index.

    Index documentation for semantic search, enabling AI agents
    to query HostKit capabilities with natural language.

    Example:
        hostkit docs index      # Build/rebuild the index
        hostkit docs status     # Show index statistics
    """
    pass


@docs.command("index")
@click.option("--force", is_flag=True, help="Force rebuild even if index exists")
@click.pass_context
def docs_index(ctx: click.Context, force: bool) -> None:
    """Index HostKit documentation for semantic search.

    Parses CLAUDE.md and capabilities output into semantic chunks,
    then indexes them in the vector store for AI agent queries.

    Example:
        hostkit docs index           # Build index
        hostkit docs index --force   # Rebuild from scratch
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DocsService()

    try:
        if not ctx.obj["json_mode"]:
            click.echo("Indexing HostKit documentation...")

        result = service.index_docs(force=force)

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Documentation indexed successfully",
                data=result,
            )
        else:
            click.echo("\nDocumentation Index Complete")
            click.echo("-" * 40)
            click.echo(f"  Total chunks: {result['chunks_total']}")
            click.echo(f"  Ingested: {result['chunks_ingested']}")
            if result["chunks_errors"] > 0:
                click.echo(f"  Errors: {result['chunks_errors']}")
            click.echo("\n  Sources:")
            click.echo(f"    CLAUDE.md: {result['sources']['claude_md']} chunks")
            click.echo(f"    Capabilities: {result['sources']['capabilities']} chunks")
            click.echo(f"\n  Indexed at: {result['indexed_at']}")
            click.echo("\nRun 'hostkit query \"<question>\"' to search.")

    except DocsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)


@docs.command("status")
@click.pass_context
def docs_status(ctx: click.Context) -> None:
    """Show documentation index status.

    Displays index statistics including chunk count,
    document count, and last indexed time.

    Example:
        hostkit docs status
        hostkit --json docs status
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DocsService()

    try:
        status = service.get_index_status()

        if ctx.obj["json_mode"]:
            formatter.success(
                message="Documentation index status",
                data=status,
            )
        else:
            click.echo("\nDocumentation Index Status")
            click.echo("-" * 40)

            if not status.get("indexed"):
                click.echo("  Status: NOT INDEXED")
                click.echo(f"\n  {status.get('message', '')}")
                if status.get("suggestion"):
                    click.echo(f"  Suggestion: {status['suggestion']}")
            else:
                click.echo("  Status: INDEXED")
                click.echo(f"  Collection: {status.get('collection', 'docs')}")
                click.echo(f"  Chunks indexed: {status.get('chunk_count', 0)}")
                click.echo(f"  Documents in store: {status.get('document_count', 0)}")
                click.echo(f"  Last indexed: {status.get('indexed_at', 'Unknown')}")
                click.echo("\nRun 'hostkit query \"<question>\"' to search.")

    except DocsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

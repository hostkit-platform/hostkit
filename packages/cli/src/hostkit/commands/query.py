"""Query command for HostKit documentation.

Semantic search over HostKit documentation for AI agents.
"""

import click

from hostkit.output import OutputFormatter
from hostkit.services.docs_service import DocsService, DocsServiceError


@click.command("query")
@click.argument("question")
@click.option("--limit", "-n", default=5, help="Number of chunks to retrieve (default: 5)")
@click.option("--raw", is_flag=True, help="Return raw chunks without LLM processing")
@click.pass_context
def query(ctx: click.Context, question: str, limit: int, raw: bool) -> None:
    """Query HostKit documentation with natural language.

    Searches indexed documentation and returns relevant answers
    with specific commands and references.

    Designed for AI agents to quickly find the right HostKit
    commands without parsing the full capabilities output.

    Examples:
        hostkit query "how do I enable payments"
        hostkit query "what environment variables does auth set"
        hostkit query "how do I deploy my project"
        hostkit --json query "enable chatbot for my project"

    Use --raw to get raw documentation chunks without LLM processing:
        hostkit query "payments" --raw
    """
    formatter: OutputFormatter = ctx.obj["formatter"]
    service = DocsService()

    try:
        result = service.query(question=question, limit=limit, raw=raw)

        if ctx.obj["json_mode"]:
            formatter.success(
                message=f"Query results for: {question}",
                data=result,
            )
        else:
            if raw:
                # Raw mode - show chunks
                click.echo(f"\nDocumentation chunks for: {question}")
                click.echo("=" * 60)

                for i, chunk in enumerate(result.get("chunks", [])):
                    click.echo(f"\n[{i + 1}] Score: {chunk.get('score', 0):.2f}")
                    click.echo(f"Source: {chunk.get('source', 'unknown')}")
                    click.echo("-" * 40)
                    content = chunk.get("content", "")[:500]
                    if len(chunk.get("content", "")) > 500:
                        content += "..."
                    click.echo(content)
            else:
                # Processed answer
                click.echo(f"\n{result.get('answer', 'No answer available')}")

                commands = result.get("commands", [])
                if commands:
                    click.echo("\nRelevant commands:")
                    for cmd in commands:
                        click.echo(f"  {cmd}")

                see_also = result.get("see_also", [])
                if see_also:
                    click.echo("\nSee also:")
                    for topic in see_also:
                        click.echo(f"  - {topic}")

                if result.get("note"):
                    click.echo(f"\nNote: {result['note']}")

                sources = result.get("sources", 0)
                if sources:
                    click.echo(f"\n({sources} documentation sources consulted)")

    except DocsServiceError as e:
        formatter.error(code=e.code, message=e.message, suggestion=e.suggestion)
        raise SystemExit(1)

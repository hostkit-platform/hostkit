"""Output formatting helpers for HostKit CLI."""

import json
import sys
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.columns import Columns
from rich.box import ROUNDED, SIMPLE
from rich.live import Live
from rich.layout import Layout


# Status indicators with colors
STATUS_INDICATORS = {
    # Success states
    "running": ("green", "●"),
    "active": ("green", "●"),
    "ok": ("green", "✓"),
    "healthy": ("green", "✓"),
    "completed": ("green", "✓"),
    "valid": ("green", "✓"),
    # Warning states
    "warning": ("yellow", "◐"),
    "degraded": ("yellow", "◐"),
    "pending": ("yellow", "○"),
    # Error states
    "stopped": ("red", "○"),
    "inactive": ("red", "○"),
    "failed": ("red", "✗"),
    "error": ("red", "✗"),
    "critical": ("red", "✗"),
    "expired": ("red", "✗"),
    # Unknown states
    "unknown": ("dim", "?"),
}


def get_status_display(status: str) -> str:
    """Get colored status display with indicator."""
    status_lower = status.lower()
    if status_lower in STATUS_INDICATORS:
        color, indicator = STATUS_INDICATORS[status_lower]
        return f"[{color}]{indicator} {status}[/{color}]"
    return status


def create_progress_bar(total: int, description: str = "Progress") -> Progress:
    """Create a progress bar for long-running operations."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=Console(),
    )


class OutputFormatter:
    """Handles output formatting for both JSON and pretty (human) modes."""

    def __init__(self, json_mode: bool = False) -> None:
        """Initialize the formatter."""
        self.json_mode = json_mode
        self.console = Console()

    def success(self, data: Any, message: str = "Operation completed") -> None:
        """Output a success response."""
        if self.json_mode:
            self._json_output(True, data=data, message=message)
        else:
            self._pretty_success(data, message)

    def error(
        self,
        code: str,
        message: str,
        suggestion: str | None = None,
        exit_code: int = 1,
    ) -> None:
        """Output an error response and exit."""
        if self.json_mode:
            self._json_output(
                False,
                error={"code": code, "message": message, "suggestion": suggestion},
            )
        else:
            self._pretty_error(code, message, suggestion)
        sys.exit(exit_code)

    def table(
        self,
        data: list[dict[str, Any]],
        columns: list[tuple[str, str]],
        title: str | None = None,
        message: str = "Data retrieved",
    ) -> None:
        """Output data as a table (pretty mode) or list (JSON mode)."""
        if self.json_mode:
            self._json_output(True, data=data, message=message)
        else:
            self._pretty_table(data, columns, title)

    def status_panel(
        self,
        title: str,
        sections: dict[str, Any],
        message: str = "Status retrieved",
    ) -> None:
        """Output a status panel with multiple sections."""
        if self.json_mode:
            self._json_output(True, data=sections, message=message)
        else:
            self._pretty_status_panel(title, sections)

    def _json_output(
        self,
        success: bool,
        data: Any = None,
        message: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Output in JSON format."""
        output: dict[str, Any] = {
            "success": success,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        if success:
            output["data"] = data
            output["message"] = message
        else:
            output["error"] = error

        print(json.dumps(output, indent=2, default=str))

    def _pretty_success(self, data: Any, message: str) -> None:
        """Output a success message in pretty format."""
        self.console.print(f"[green]{message}[/green]")

        if isinstance(data, dict):
            for key, value in data.items():
                self.console.print(f"  [cyan]{key}:[/cyan] {value}")
        elif isinstance(data, list):
            for item in data:
                self.console.print(f"  - {item}")
        elif data is not None:
            self.console.print(f"  {data}")

    def _pretty_error(self, code: str, message: str, suggestion: str | None) -> None:
        """Output an error in pretty format."""
        error_text = Text()
        error_text.append("Error: ", style="bold red")
        error_text.append(f"[{code}] ", style="red")
        error_text.append(message)

        self.console.print(error_text)

        if suggestion:
            self.console.print(f"[yellow]Suggestion:[/yellow] {suggestion}")

    def _pretty_table(
        self,
        data: list[dict[str, Any]],
        columns: list[tuple[str, str]],
        title: str | None,
    ) -> None:
        """Output data as a pretty table."""
        if not data:
            self.console.print("[dim]No data to display[/dim]")
            return

        table = Table(title=title, show_header=True, header_style="bold cyan")

        for col_key, col_header in columns:
            table.add_column(col_header)

        for row in data:
            table.add_row(*[str(row.get(col_key, "")) for col_key, _ in columns])

        self.console.print(table)

    def _pretty_status_panel(self, title: str, sections: dict[str, Any]) -> None:
        """Output a status panel with multiple sections using enhanced visualization."""
        # Print main title
        self.console.print()
        self.console.print(f"[bold cyan]╔══ {title} ══╗[/bold cyan]")
        self.console.print()

        for section_name, section_data in sections.items():
            section_title = section_name.replace("_", " ").title()

            if isinstance(section_data, dict):
                # Create a mini-table for dict data with enhanced styling
                table = Table(
                    show_header=False,
                    box=ROUNDED,
                    padding=(0, 1),
                    title=f"[bold]{section_title}[/bold]",
                    title_style="cyan",
                    border_style="dim",
                )
                table.add_column("Key", style="cyan", width=20)
                table.add_column("Value")

                for key, value in section_data.items():
                    # Format status values with enhanced indicators
                    display_value = str(value)
                    if isinstance(value, str):
                        display_value = get_status_display(value)
                    elif isinstance(value, int) and key.lower() in ("errors_24h", "error_count_24h"):
                        # Highlight error counts
                        if value > 0:
                            display_value = f"[red]{value}[/red]"
                        else:
                            display_value = f"[green]{value}[/green]"
                    elif isinstance(value, int) and key.lower() in ("warnings_24h", "warning_count_24h"):
                        # Highlight warning counts
                        if value > 0:
                            display_value = f"[yellow]{value}[/yellow]"
                        else:
                            display_value = f"[green]{value}[/green]"

                    table.add_row(key.replace("_", " ").title(), display_value)

                self.console.print(table)

            elif isinstance(section_data, list):
                # Create a table for list data
                if section_data and isinstance(section_data[0], dict):
                    table = Table(
                        title=f"[bold]{section_title}[/bold]",
                        show_header=True,
                        header_style="bold cyan",
                        box=ROUNDED,
                        border_style="dim",
                    )

                    # Auto-detect columns from first item
                    for key in section_data[0].keys():
                        table.add_column(key.replace("_", " ").title())

                    for item in section_data:
                        values = []
                        for value in item.values():
                            if isinstance(value, str):
                                values.append(get_status_display(value))
                            else:
                                values.append(str(value))
                        table.add_row(*values)

                    self.console.print(table)
                elif section_data:
                    self.console.print(f"[bold cyan]{section_title}:[/bold cyan]")
                    for item in section_data:
                        self.console.print(f"  [dim]•[/dim] {item}")
                else:
                    self.console.print(f"[bold cyan]{section_title}:[/bold cyan] [dim]None[/dim]")

            else:
                self.console.print(f"[bold cyan]{section_title}:[/bold cyan] {section_data}")

            self.console.print()  # Blank line between sections

    def progress_context(self, description: str = "Processing"):
        """Create a progress context for long-running operations.

        Usage:
            with formatter.progress_context("Deploying") as progress:
                for item in items:
                    # do work
                    progress.advance()
        """
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
        )

    def spinner(self, message: str = "Working"):
        """Create a spinner context for operations with unknown duration.

        Usage:
            with formatter.spinner("Creating backup"):
                # do work
        """
        return self.console.status(f"[bold cyan]{message}...[/bold cyan]")


def format_bytes(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_uptime(seconds: float) -> str:
    """Format uptime in seconds to human-readable string."""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")

    return " ".join(parts)

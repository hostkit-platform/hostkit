"""Base tool class and shared types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class ToolTier(Enum):
    """Tool risk tiers."""

    READ_ONLY = 1  # Safe, read-only operations
    STATE_CHANGE = 2  # Modifies state, reversible
    DESTRUCTIVE = 3  # High-risk, potentially irreversible


@dataclass
class ToolResult:
    """Result from tool execution."""

    success: bool
    output: str  # Human-readable output
    data: dict | None = None  # Structured data for programmatic use
    error: str | None = None
    truncated: bool = False  # True if output was truncated

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "output": self.output,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        if self.truncated:
            result["truncated"] = True
        return result


@dataclass
class ToolDefinition:
    """Anthropic API tool definition format."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class BaseTool(ABC):
    """Abstract base class for all tools.

    Each tool must define:
    - name: Unique identifier (e.g., "logs", "db:read")
    - description: Human-readable description for Claude
    - tier: Risk level (1=read-only, 2=state-change, 3=destructive)
    - input_schema: JSON Schema for parameters
    - execute(): Async method that performs the operation
    """

    name: str
    description: str
    tier: ToolTier
    input_schema: dict

    # Maximum output size in characters (50KB)
    MAX_OUTPUT_SIZE = 50000

    @abstractmethod
    async def execute(self, project_name: str, **params: Any) -> ToolResult:
        """Execute the tool with given parameters.

        Args:
            project_name: The project context for the operation
            **params: Tool-specific parameters matching input_schema

        Returns:
            ToolResult with success status and output
        """
        pass

    @classmethod
    def get_definition(cls) -> dict:
        """Get Anthropic-format tool definition."""
        return {
            "name": cls.name,
            "description": cls.description,
            "input_schema": cls.input_schema,
        }

    @classmethod
    def truncate_output(cls, output: str) -> tuple[str, bool]:
        """Truncate output if it exceeds MAX_OUTPUT_SIZE.

        Returns:
            Tuple of (output, was_truncated)
        """
        if len(output) <= cls.MAX_OUTPUT_SIZE:
            return output, False

        # Truncate and add indicator
        truncated = output[: cls.MAX_OUTPUT_SIZE - 100]
        truncated += f"\n\n[Output truncated - {len(output) - cls.MAX_OUTPUT_SIZE + 100} characters omitted]"
        return truncated, True

    @staticmethod
    def format_error(error: Exception) -> str:
        """Format an exception for tool output."""
        error_type = type(error).__name__
        return f"{error_type}: {str(error)}"

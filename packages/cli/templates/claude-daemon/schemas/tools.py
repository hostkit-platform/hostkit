"""Tool-related schemas."""

from pydantic import BaseModel


class ToolParameter(BaseModel):
    """A parameter for a tool."""
    name: str
    type: str
    description: str
    required: bool = True


class ToolDefinition(BaseModel):
    """Definition of an available tool."""
    name: str
    description: str
    tier: int  # 1=low risk, 2=medium, 3=high
    parameters: list[ToolParameter]


class ToolsListResponse(BaseModel):
    """Response for listing available tools."""
    success: bool = True
    data: dict  # {"tools": list[ToolDefinition]}

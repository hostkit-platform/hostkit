"""Chat-related schemas."""

from typing import Literal
from pydantic import BaseModel, Field
from uuid import UUID


class ChatMessage(BaseModel):
    """A message in the chat."""
    role: Literal["user", "assistant", "tool_result"]
    content: str


class ToolCall(BaseModel):
    """A tool call made by the assistant."""
    id: str
    name: str
    input: dict


class ChatRequest(BaseModel):
    """Request to send a chat message."""
    conversation_id: UUID | None = Field(
        default=None,
        description="Existing conversation ID, or omit to create new"
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="Messages to send",
        min_length=1
    )
    tools: list[str] | None = Field(
        default=None,
        description="Tools to enable (must be granted to project)"
    )
    auto_execute: bool = Field(
        default=False,
        description="Automatically execute tool calls"
    )
    stream: bool = Field(
        default=False,
        description="Stream the response"
    )
    system_prompt: str | None = Field(
        default=None,
        description="System prompt (only used for new conversations)"
    )


class UsageInfo(BaseModel):
    """Token usage information."""
    input_tokens: int
    output_tokens: int


class ToolResultData(BaseModel):
    """Result from a tool execution."""
    tool_call_id: str
    tool_name: str
    success: bool
    output: str
    error: str | None = None


class ChatResponseData(BaseModel):
    """Data returned from a chat request."""
    conversation_id: UUID
    message: ChatMessage
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResultData] | None = None
    usage: UsageInfo


class ChatResponse(BaseModel):
    """Response from chat endpoint."""
    success: bool = True
    data: ChatResponseData

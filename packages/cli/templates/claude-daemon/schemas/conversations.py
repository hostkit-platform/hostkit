"""Conversation-related schemas."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel

from schemas.chat import ChatMessage


class ConversationSummary(BaseModel):
    """Summary of a conversation for listing."""
    id: UUID
    title: str | None
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
    created_at: datetime
    updated_at: datetime


class ConversationDetail(BaseModel):
    """Full conversation detail with messages."""
    id: UUID
    title: str | None
    system_prompt: str | None
    message_count: int
    total_input_tokens: int
    total_output_tokens: int
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessage]


class ConversationsListResponse(BaseModel):
    """Response for listing conversations."""
    success: bool = True
    data: dict  # {"conversations": list[ConversationSummary]}


class ConversationResponse(BaseModel):
    """Response for single conversation."""
    success: bool = True
    data: ConversationDetail

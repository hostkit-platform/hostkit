"""Conversations router for chatbot service - view conversation history."""

import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from config import get_settings
from database import get_cursor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chatbot/conversations", tags=["conversations"])


class MessageResponse(BaseModel):
    """Message in a conversation."""

    id: str
    role: str
    content: str
    created_at: datetime
    tokens_used: Optional[int] = None


class ConversationResponse(BaseModel):
    """Conversation details."""

    id: str
    session_id: str
    status: str
    message_count: int
    started_at: datetime
    last_message_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class ConversationWithMessages(ConversationResponse):
    """Conversation with message history."""

    messages: list[MessageResponse] = []


def validate_api_key(request: Request) -> bool:
    """Validate API key from request headers or query params."""
    settings = get_settings()

    # Check header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token == settings.api_key:
            return True

    # Check query param
    api_key = request.query_params.get("api_key")
    if api_key == settings.api_key:
        return True

    return False


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    api_key: str = Query(None, description="API key for authentication"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """List conversations for this project."""
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Build query
    query = """
        SELECT id, session_id, status, message_count, started_at, last_message_at, ended_at
        FROM chatbot_conversations
        WHERE project = %s
    """
    params = [settings.project_name]

    if session_id:
        query += " AND session_id = %s"
        params.append(session_id)

    if status:
        query += " AND status = %s"
        params.append(status)

    query += " ORDER BY started_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    with get_cursor() as cursor:
        cursor.execute(query, params)
        conversations = cursor.fetchall()

    return [
        ConversationResponse(
            id=str(c["id"]),
            session_id=c["session_id"],
            status=c["status"],
            message_count=c["message_count"],
            started_at=c["started_at"],
            last_message_at=c["last_message_at"],
            ended_at=c["ended_at"],
        )
        for c in conversations
    ]


@router.get("/{conversation_id}", response_model=ConversationWithMessages)
async def get_conversation(
    conversation_id: str,
    request: Request,
    api_key: str = Query(None, description="API key for authentication"),
):
    """Get a specific conversation with its messages."""
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    with get_cursor() as cursor:
        # Get conversation
        cursor.execute(
            """
            SELECT id, session_id, status, message_count, started_at, last_message_at, ended_at
            FROM chatbot_conversations
            WHERE id = %s AND project = %s
            """,
            [conversation_id, settings.project_name],
        )
        conversation = cursor.fetchone()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Get messages
        cursor.execute(
            """
            SELECT id, role, content, created_at, tokens_used
            FROM chatbot_messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC
            """,
            [conversation_id],
        )
        messages = cursor.fetchall()

    return ConversationWithMessages(
        id=str(conversation["id"]),
        session_id=conversation["session_id"],
        status=conversation["status"],
        message_count=conversation["message_count"],
        started_at=conversation["started_at"],
        last_message_at=conversation["last_message_at"],
        ended_at=conversation["ended_at"],
        messages=[
            MessageResponse(
                id=str(m["id"]),
                role=m["role"],
                content=m["content"],
                created_at=m["created_at"],
                tokens_used=m["tokens_used"],
            )
            for m in messages
        ],
    )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    api_key: str = Query(None, description="API key for authentication"),
):
    """Delete a conversation and all its messages."""
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    with get_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM chatbot_conversations
            WHERE id = %s AND project = %s
            RETURNING id
            """,
            [conversation_id, settings.project_name],
        )
        result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conversation_id}

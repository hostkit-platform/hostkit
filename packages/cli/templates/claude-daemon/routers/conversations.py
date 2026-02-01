"""Conversations router - manage conversation threads."""

from uuid import UUID
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, func

from dependencies import DB, CurrentProject
from models.conversation import Conversation
from models.message import Message
from schemas.conversations import (
    ConversationSummary,
    ConversationDetail,
    ConversationsListResponse,
    ConversationResponse,
)
from schemas.chat import ChatMessage

router = APIRouter(prefix="/v1", tags=["Conversations"])


@router.get("/conversations")
async def list_conversations(
    project: CurrentProject,
    db: DB,
    limit: int = 50,
    offset: int = 0,
):
    """List conversations for the current project.

    Returns a paginated list of conversations ordered by most recent activity.
    """
    result = await db.execute(
        select(Conversation)
        .where(Conversation.project_name == project.project_name)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    conversations = result.scalars().all()

    # Get total count
    count_result = await db.execute(
        select(func.count(Conversation.id))
        .where(Conversation.project_name == project.project_name)
    )
    total = count_result.scalar()

    return {
        "success": True,
        "data": {
            "conversations": [
                ConversationSummary(
                    id=c.id,
                    title=c.title,
                    message_count=c.message_count,
                    total_input_tokens=c.total_input_tokens,
                    total_output_tokens=c.total_output_tokens,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                ).model_dump()
                for c in conversations
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    project: CurrentProject,
    db: DB,
):
    """Get a conversation with all its messages."""
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.project_name == project.project_name,
        )
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Conversation '{conversation_id}' not found",
                }
            }
        )

    # Get messages
    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = messages_result.scalars().all()

    return {
        "success": True,
        "data": ConversationDetail(
            id=conversation.id,
            title=conversation.title,
            system_prompt=conversation.system_prompt,
            message_count=conversation.message_count,
            total_input_tokens=conversation.total_input_tokens,
            total_output_tokens=conversation.total_output_tokens,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=[
                ChatMessage(role=m.role, content=m.content)
                for m in messages
            ],
        ).model_dump()
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    project: CurrentProject,
    db: DB,
):
    """Delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.project_name == project.project_name,
        )
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Conversation '{conversation_id}' not found",
                }
            }
        )

    await db.delete(conversation)

    return {
        "success": True,
        "message": f"Conversation '{conversation_id}' deleted",
    }

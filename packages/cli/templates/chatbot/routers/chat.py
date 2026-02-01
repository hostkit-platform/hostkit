"""Chat router for chatbot service - handles message sending with SSE streaming."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from config import get_settings
from database import get_cursor, get_db
from providers.llm import get_llm_provider, LLMStreamHandler

logger = logging.getLogger(__name__)


def process_streaming_token(buffer: str, token: str) -> str:
    """Process a streaming token to handle BPE tokenization artifacts.

    BPE tokenizers encode word boundaries, punctuation, and suffixes with
    space prefixes/suffixes. This function strips spurious spaces to produce
    natural prose.

    Examples:
        - "Hall" + " ucination" → "Hallucination" (word continuation)
        - "retrieve" + " d" → "retrieved" (suffix)
        - "overview" + " :" → "overview:" (punctuation)
        - "Retrieval" + "- " + "Aug" → "Retrieval-Aug" (hyphen compound)
        - "Hello" + " world" → "Hello world" (preserved - true word boundary)
    """
    if not token:
        return token

    last_char = buffer[-1] if buffer else ""

    # DEBUG: Log tokens for analysis
    if ' ' in token or (buffer and buffer[-5:].replace(' ', '') in ['relev', 'anand', 'Chunk']):
        logger.debug(f"TOKEN: buffer_tail={repr(buffer[-10:] if buffer else '')} token={repr(token)}")

    # Case 1: Punctuation tokens with leading space → strip space
    # " :" " ," " ." " ;" " /" " )" " ?" " !"
    if token in (' :', ' ,', ' .', ' ;', ' /', ' )', ' ?', ' !', ' %'):
        return token[1:]

    # Case 2: Hyphen/dash with trailing space (compounds like "Retrieval- Aug")
    # "- " after alphanumeric → strip trailing space
    if token in ('- ', '– ', '— ') and last_char.isalnum():
        return token[0]

    # Case 3: Opening paren/bracket with trailing space → strip trailing
    if token in ('( ', '[ ', '{ '):
        return token[0]

    # Case 4: Markdown bold/italic with adjacent space → strip space
    if token in (' **', '** '):
        return '**'
    if token in (' *', '* ') and last_char != '*':  # Avoid breaking "**"
        return '*'
    if token in (' `', '` '):
        return '`'

    # Case 5: Slash/ampersand spacing (Q&A, and/or)
    if token == ' &' or token == '& ':
        return '&'

    # For remaining cases, need leading space check
    if not token.startswith(' ') or len(token) < 2:
        return token

    first_content = token[1:]

    # Case 6: Short suffix continuation - space + short lowercase after letter
    # Catches: " d", " ed", " ing", " ly", " er", " est", etc.
    # Max 3 chars to avoid stripping real words. For 4-char, use explicit suffix list.
    # Common 4-letter words that should KEEP the space (NOT suffixes)
    COMMON_WORDS = {
        'such', 'that', 'this', 'with', 'from', 'have', 'been', 'were', 'will',
        'more', 'some', 'than', 'them', 'then', 'when', 'what', 'your', 'into',
        'also', 'like', 'just', 'only', 'over', 'most', 'make', 'made', 'even',
        'each', 'much', 'both', 'does', 'here', 'well', 'back', 'used', 'data',
        'very', 'need', 'same', 'work', 'part', 'take', 'come', 'many', 'long',
    }
    if last_char.isalpha() and first_content.islower() and len(first_content) <= 3:
        return first_content
    # 4-char: only strip if NOT a common word
    if last_char.isalpha() and first_content.islower() and len(first_content) == 4:
        if first_content not in COMMON_WORDS:
            return first_content

    # Case 7: Known suffix patterns (longer suffixes that are clearly word parts)
    KNOWN_SUFFIXES = {
        # -ation/-ition/-ution/-ction endings
        'ation', 'ition', 'ution', 'ction', 'mentation', 'mentation',
        # Common suffixes
        'ment', 'ments', 'ness', 'less', 'ful', 'fully',
        'able', 'ible', 'ive', 'ives', 'ous', 'ously',
        'ally', 'ingly', 'edly', 'ately',
        'ized', 'ised', 'izing', 'ising', 'ization', 'isation',
        'ology', 'ography', 'ological',
        # BPE common splits
        'ances', 'ences', 'anted', 'ented', 'mented', 'verted',
        'reshold', 'aining', 'aining', 'ording', 'arding',
        'ucination', 'igence', 'igation', 'ounding', 'ancing',
        'rieval', 'tering', 'tering', 'ration', 'rations',
        'orithm', 'orithms',  # algorithm splits
        'bedding', 'beddings',  # embedding splits
    }
    if last_char.isalpha() and first_content.lower() in KNOWN_SUFFIXES:
        return first_content

    # Case 8: Punctuation attachment - "word" + " ." or "It" + " '"
    if last_char.isalnum() and token[1] in ".,;:!?'\"'":
        return first_content

    # Case 9: After hyphen/dash - "Retrieval-" + " Aug"
    if last_char in "-–—" and token[1].isalnum():
        return first_content

    # Default: preserve token as-is (true word boundary)
    return token


router = APIRouter(prefix="/chatbot", tags=["chat"])


class ChatMessage(BaseModel):
    """Incoming chat message."""

    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(..., min_length=1, max_length=64)
    visitor_id: Optional[str] = Field(None, max_length=64)
    page_url: Optional[str] = Field(None, max_length=2000)


class ChatResponse(BaseModel):
    """Chat response metadata."""

    conversation_id: str
    message_id: str
    session_id: str


def get_or_create_conversation(
    project: str,
    session_id: str,
    visitor_id: Optional[str] = None,
    page_url: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    """Get existing active conversation or create a new one."""
    with get_cursor() as cursor:
        # Look for active conversation
        cursor.execute(
            """
            SELECT id, message_count, status
            FROM chatbot_conversations
            WHERE project = %s AND session_id = %s AND status = 'active'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            [project, session_id],
        )
        conversation = cursor.fetchone()

        if conversation:
            return dict(conversation)

        # Create new conversation
        conversation_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO chatbot_conversations (
                id, project, session_id, visitor_id, page_url,
                user_agent, ip_address, status, message_count, started_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', 0, NOW())
            RETURNING id, message_count, status
            """,
            [conversation_id, project, session_id, visitor_id, page_url, user_agent, ip_address],
        )
        return dict(cursor.fetchone())


def get_conversation_history(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get conversation history for context."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT role, content
            FROM chatbot_messages
            WHERE conversation_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [conversation_id, limit],
        )
        messages = cursor.fetchall()
        # Reverse to get chronological order
        return [dict(m) for m in reversed(messages)]


def get_chatbot_config(project: str) -> dict:
    """Get chatbot configuration for a project."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM chatbot_configs
            WHERE project = %s OR project = '_default'
            ORDER BY CASE WHEN project = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            [project, project],
        )
        config = cursor.fetchone()
        if config:
            return dict(config)
        return {
            "name": "Assistant",
            "system_prompt": None,
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "temperature": 0.7,
        }


def save_message(
    conversation_id: str,
    project: str,
    role: str,
    content: str,
    tokens_used: Optional[int] = None,
    model_used: Optional[str] = None,
    latency_ms: Optional[int] = None,
    error: bool = False,
    error_message: Optional[str] = None,
) -> str:
    """Save a message to the database."""
    message_id = str(uuid.uuid4())
    with get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chatbot_messages (
                id, conversation_id, project, role, content,
                tokens_used, model_used, latency_ms, error, error_message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                message_id, conversation_id, project, role, content,
                tokens_used, model_used, latency_ms, error, error_message,
            ],
        )

        # Update conversation
        cursor.execute(
            """
            UPDATE chatbot_conversations
            SET message_count = message_count + 1, last_message_at = NOW()
            WHERE id = %s
            """,
            [conversation_id],
        )

    return message_id


def check_rate_limit(project: str, identifier: str, limit: int, window_seconds: int) -> bool:
    """Check if request is within rate limits. Returns True if allowed."""
    window_start = datetime.utcnow().replace(second=0, microsecond=0)

    with get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO chatbot_rate_limits (project, identifier, window_start, request_count)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (project, identifier, window_start)
            DO UPDATE SET request_count = chatbot_rate_limits.request_count + 1
            RETURNING request_count
            """,
            [project, identifier, window_start],
        )
        result = cursor.fetchone()
        count = result["request_count"] if result else 0

        return count <= limit


def validate_api_key(request: Request) -> bool:
    """Validate API key from request headers or query params."""
    settings = get_settings()

    # Check header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token == settings.api_key:
            return True

    # Check query param (for widget)
    api_key = request.query_params.get("api_key")
    if api_key == settings.api_key:
        return True

    return False


@router.post("/chat", response_model=ChatResponse)
async def send_message(
    message: ChatMessage,
    request: Request,
    api_key: str = Query(None, description="API key for authentication"),
):
    """Send a chat message and get a response.

    Returns message metadata. Use the /chat/stream endpoint for SSE streaming.
    """
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(
        settings.project_name,
        message.session_id or client_ip,
        settings.rate_limit_messages,
        settings.rate_limit_window,
    ):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Get or create conversation
    conversation = get_or_create_conversation(
        project=settings.project_name,
        session_id=message.session_id,
        visitor_id=message.visitor_id,
        page_url=message.page_url,
        user_agent=request.headers.get("User-Agent"),
        ip_address=client_ip,
    )

    # Save user message
    user_message_id = save_message(
        conversation_id=conversation["id"],
        project=settings.project_name,
        role="user",
        content=message.message,
    )

    return ChatResponse(
        conversation_id=str(conversation["id"]),
        message_id=user_message_id,
        session_id=message.session_id,
    )


@router.get("/chat/stream")
async def stream_response(
    request: Request,
    conversation_id: str = Query(..., description="Conversation ID"),
    api_key: str = Query(None, description="API key for authentication"),
):
    """Stream a chat response using Server-Sent Events.

    Call this after /chat to get the assistant's response streamed.
    """
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Get conversation history
    history = get_conversation_history(conversation_id)
    if not history:
        raise HTTPException(status_code=404, detail="Conversation not found or empty")

    # Get chatbot config
    config = get_chatbot_config(settings.project_name)

    # Build messages for LLM
    messages = []

    # Add system prompt if configured
    system_prompt = config.get("system_prompt")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Add conversation history
    messages.extend(history)

    # Get LLM provider
    llm = get_llm_provider(config.get("llm_provider", "anthropic"))

    async def event_generator():
        """Generate SSE events from LLM stream."""
        start_time = datetime.utcnow()
        full_response = ""
        emitted_buffer = ""  # Track what client has received for detokenization
        tokens_used = 0

        try:
            async for chunk in llm.stream(
                messages=messages,
                model=config.get("llm_model", "claude-sonnet-4-20250514"),
                max_tokens=config.get("max_tokens", 1024),
                temperature=float(config.get("temperature", 0.7)),
            ):
                if chunk.get("type") == "content":
                    raw_token = chunk["text"]
                    full_response += raw_token  # DB gets raw concatenation (correct)

                    # Clean carriage returns that break SSE parsing and markdown rendering
                    # Normalize \r\n to \n first (preserve line breaks), then strip lone \r
                    display_token = raw_token.replace('\r\n', '\n').replace('\r', '')

                    yield {
                        "event": "message",
                        "data": display_token,
                    }
                elif chunk.get("type") == "usage":
                    tokens_used = chunk.get("total_tokens", 0)

            # Calculate latency
            latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Save assistant message
            save_message(
                conversation_id=conversation_id,
                project=settings.project_name,
                role="assistant",
                content=full_response,
                tokens_used=tokens_used,
                model_used=config.get("llm_model"),
                latency_ms=latency_ms,
            )

            # Send done event
            yield {
                "event": "done",
                "data": "stream_complete",
            }

        except Exception as e:
            logger.error(f"LLM streaming error: {e}")

            # Save error message
            save_message(
                conversation_id=conversation_id,
                project=settings.project_name,
                role="assistant",
                content="I'm sorry, I encountered an error processing your message.",
                error=True,
                error_message=str(e),
            )

            yield {
                "event": "error",
                "data": "An error occurred while generating a response",
            }

    # Use LF line endings (sep="\n") instead of default CRLF ("\r\n")
    # Some EventSource parsers don't strip \r, leaving it in the data
    return EventSourceResponse(event_generator(), sep="\n")


@router.post("/chat/end")
async def end_conversation(
    request: Request,
    conversation_id: str = Query(..., description="Conversation ID"),
    api_key: str = Query(None, description="API key for authentication"),
):
    """End a conversation."""
    settings = get_settings()

    # Validate API key
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    with get_cursor() as cursor:
        cursor.execute(
            """
            UPDATE chatbot_conversations
            SET status = 'ended', ended_at = NOW()
            WHERE id = %s AND project = %s
            RETURNING id
            """,
            [conversation_id, settings.project_name],
        )
        result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "ended", "conversation_id": conversation_id}

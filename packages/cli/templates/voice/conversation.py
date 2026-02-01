"""Call session state management."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class CallState(str, Enum):
    """Call states."""
    CONNECTING = "connecting"
    ACTIVE = "active"
    ENDED = "ended"
    ERROR = "error"


@dataclass
class Message:
    """Conversation message."""
    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CallSession:
    """Per-call state container."""

    call_sid: str
    project: str
    agent_name: str
    agent_config: Dict[str, Any]

    state: CallState = CallState.CONNECTING
    messages: List[Message] = field(default_factory=list)

    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None

    # Streaming state
    current_utterance: str = ""
    is_speaking: bool = False

    def add_message(self, role: str, content: str):
        """Add message to conversation history."""
        self.messages.append(Message(role=role, content=content))

    def get_context(self) -> List[Dict[str, str]]:
        """Get conversation context for LLM."""
        context = []

        # System prompt
        if "system_prompt" in self.agent_config:
            context.append({
                "role": "system",
                "content": self.agent_config["system_prompt"]
            })

        # Conversation history
        for msg in self.messages:
            context.append({
                "role": msg.role,
                "content": msg.content
            })

        return context

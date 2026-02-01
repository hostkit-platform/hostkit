"""LLM Provider abstraction for chatbot service.

Supports:
- Anthropic (Claude)
- OpenAI (GPT-4)

Uses async streaming for real-time SSE responses.
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


class LLMStreamHandler(ABC):
    """Abstract handler for LLM streaming responses."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Stream LLM response.

        Yields chunks with format:
        - {"type": "content", "text": "chunk text"}
        - {"type": "usage", "total_tokens": 123}
        """
        pass


class AnthropicProvider(LLMStreamHandler):
    """Anthropic Claude provider."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

    async def stream(
        self,
        messages: list[dict],
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Stream response from Claude."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        # Extract system message if present
        system = system_prompt
        filtered_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                filtered_messages.append(msg)

        try:
            async with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "You are a helpful assistant.",
                messages=filtered_messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield {"type": "content", "text": text}

                # Get final message for usage stats
                final_message = await stream.get_final_message()
                yield {
                    "type": "usage",
                    "total_tokens": final_message.usage.input_tokens + final_message.usage.output_tokens,
                    "input_tokens": final_message.usage.input_tokens,
                    "output_tokens": final_message.usage.output_tokens,
                }

        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            raise


class OpenAIProvider(LLMStreamHandler):
    """OpenAI GPT provider."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")

    async def stream(
        self,
        messages: list[dict],
        model: str = "gpt-4-turbo-preview",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Stream response from OpenAI."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key)

        # Add system message if not present
        formatted_messages = []
        has_system = any(m["role"] == "system" for m in messages)

        if not has_system and system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        elif not has_system:
            formatted_messages.append({"role": "system", "content": "You are a helpful assistant."})

        formatted_messages.extend(messages)

        total_tokens = 0

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=formatted_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield {"type": "content", "text": chunk.choices[0].delta.content}

                if chunk.usage:
                    total_tokens = chunk.usage.total_tokens

            yield {
                "type": "usage",
                "total_tokens": total_tokens,
            }

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise


class LLMProvider:
    """Factory for LLM providers."""

    _providers = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
    }

    @classmethod
    def get(cls, provider_name: str, api_key: Optional[str] = None) -> LLMStreamHandler:
        """Get an LLM provider by name."""
        provider_class = cls._providers.get(provider_name.lower())
        if not provider_class:
            raise ValueError(f"Unknown LLM provider: {provider_name}")
        return provider_class(api_key=api_key)


def get_llm_provider(provider_name: str = "anthropic", api_key: Optional[str] = None) -> LLMStreamHandler:
    """Get an LLM provider instance.

    Args:
        provider_name: "anthropic" or "openai"
        api_key: Optional API key (uses env var if not provided)

    Returns:
        LLMStreamHandler instance
    """
    return LLMProvider.get(provider_name, api_key)

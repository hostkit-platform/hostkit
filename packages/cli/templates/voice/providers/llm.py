"""LLM providers (OpenAI, Anthropic)."""
from typing import List, Dict, AsyncIterator
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic


class LLMProvider:
    """Unified LLM interface."""

    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider
        self.model = model

        if provider == "openai":
            self.client = AsyncOpenAI(api_key=api_key)
        elif provider == "anthropic":
            self.client = AsyncAnthropic(api_key=api_key)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    async def generate(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """Generate streaming response.

        Yields text chunks as they arrive.
        """
        if self.provider == "openai":
            async for chunk in self._generate_openai(messages):
                yield chunk
        elif self.provider == "anthropic":
            async for chunk in self._generate_anthropic(messages):
                yield chunk

    async def _generate_openai(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """OpenAI streaming."""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def _generate_anthropic(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """Anthropic streaming."""
        # Extract system message
        system = None
        user_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                user_messages.append(msg)

        async with self.client.messages.stream(
            model=self.model,
            messages=user_messages,
            system=system,
            max_tokens=1024
        ) as stream:
            async for text in stream.text_stream:
                yield text

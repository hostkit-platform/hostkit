"""Cartesia TTS provider."""
import httpx
from typing import AsyncIterator


class CartesiaTTS:
    """Cartesia streaming TTS."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.cartesia.ai/tts/bytes"

    async def synthesize(self, text: str, voice_id: str = "default") -> AsyncIterator[bytes]:
        """Stream audio bytes from text.

        Yields PCM audio chunks (16-bit, 8kHz mono).
        """
        headers = {
            "X-API-Key": self.api_key,
            "Cartesia-Version": "2025-04-16",
            "Content-Type": "application/json"
        }

        payload = {
            "model_id": "sonic-3",
            "transcript": text,
            "voice": {
                "mode": "id",
                "id": voice_id or "a0e99841-438c-4a64-b679-ae501e7d6091"  # Helpful Woman
            },
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 8000
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", self.base_url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk

"""
OpenAI embedding service.

Handles embedding generation with:
- Batching for efficiency
- Retry logic for transient errors
- Token tracking
"""

import asyncio
from typing import List, Optional
from dataclasses import dataclass
import logging

from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError

from config import settings
from .tokenizer import count_tokens

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    embeddings: List[List[float]]
    tokens_used: int
    model: str


class EmbeddingService:
    """
    Service for generating embeddings via OpenAI API.

    Features:
    - Async batch processing
    - Automatic retry with exponential backoff
    - Token usage tracking
    """

    # OpenAI batch limits
    MAX_BATCH_SIZE = 100
    MAX_TOKENS_PER_BATCH = 8191  # text-embedding-3-small limit

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.dimensions = settings.OPENAI_EMBEDDING_DIMENSIONS

    async def embed_texts(
        self,
        texts: List[str],
        retry_count: int = 3,
    ) -> EmbeddingResult:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of texts to embed
            retry_count: Number of retries on failure

        Returns:
            EmbeddingResult with embeddings and usage info
        """
        if not texts:
            return EmbeddingResult(embeddings=[], tokens_used=0, model=self.model)

        # Split into batches
        batches = self._create_batches(texts)

        all_embeddings = []
        total_tokens = 0

        for batch in batches:
            result = await self._embed_batch(batch, retry_count)
            all_embeddings.extend(result.embeddings)
            total_tokens += result.tokens_used

        return EmbeddingResult(
            embeddings=all_embeddings,
            tokens_used=total_tokens,
            model=self.model,
        )

    async def embed_single(self, text: str) -> tuple[List[float], int]:
        """
        Generate embedding for a single text.

        Returns:
            Tuple of (embedding vector, tokens used)
        """
        result = await self.embed_texts([text])
        return result.embeddings[0], result.tokens_used

    def _create_batches(self, texts: List[str]) -> List[List[str]]:
        """Split texts into batches respecting size limits."""
        batches = []
        current_batch = []
        current_tokens = 0

        for text in texts:
            text_tokens = count_tokens(text)

            # Check if adding this text would exceed limits
            if (len(current_batch) >= self.MAX_BATCH_SIZE or
                current_tokens + text_tokens > self.MAX_TOKENS_PER_BATCH):

                if current_batch:
                    batches.append(current_batch)
                current_batch = [text]
                current_tokens = text_tokens
            else:
                current_batch.append(text)
                current_tokens += text_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    async def _embed_batch(
        self,
        texts: List[str],
        retry_count: int,
    ) -> EmbeddingResult:
        """Embed a single batch with retry logic."""
        last_error = None

        for attempt in range(retry_count):
            try:
                response = await self.client.embeddings.create(
                    input=texts,
                    model=self.model,
                    dimensions=self.dimensions,
                )

                embeddings = [item.embedding for item in response.data]
                tokens_used = response.usage.total_tokens

                return EmbeddingResult(
                    embeddings=embeddings,
                    tokens_used=tokens_used,
                    model=self.model,
                )

            except RateLimitError as e:
                last_error = e
                wait_time = 2 ** attempt  # Exponential backoff
                logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{retry_count})")
                await asyncio.sleep(wait_time)

            except APIConnectionError as e:
                last_error = e
                wait_time = 2 ** attempt
                logger.warning(f"Connection error, waiting {wait_time}s (attempt {attempt + 1}/{retry_count})")
                await asyncio.sleep(wait_time)

            except APIError as e:
                last_error = e
                if e.status_code >= 500:
                    wait_time = 2 ** attempt
                    logger.warning(f"Server error, waiting {wait_time}s (attempt {attempt + 1}/{retry_count})")
                    await asyncio.sleep(wait_time)
                else:
                    # Client error, don't retry
                    raise

        # All retries exhausted
        raise last_error


# Singleton instance
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get or create embedding service singleton."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service

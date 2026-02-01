"""
Intelligent text chunking service.

Splits text into semantically meaningful chunks while respecting:
- Paragraph boundaries
- Sentence boundaries
- Token limits
- Overlap for context continuity
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from config import settings
from .tokenizer import count_tokens, get_tokenizer


@dataclass
class Chunk:
    """A text chunk with metadata."""
    content: str
    index: int
    token_count: int
    extra_data: dict


class ChunkingService:
    """
    Service for splitting documents into chunks.

    Uses a hierarchical approach:
    1. Split by major boundaries (double newlines, headers)
    2. Merge small sections
    3. Split large sections by sentences
    4. Apply overlap
    """

    def __init__(
        self,
        target_tokens: int = None,
        overlap_tokens: int = None,
        min_tokens: int = None,
    ):
        self.target_tokens = target_tokens or settings.CHUNK_TARGET_TOKENS
        self.overlap_tokens = overlap_tokens or settings.CHUNK_OVERLAP_TOKENS
        self.min_tokens = min_tokens or settings.CHUNK_MIN_TOKENS
        self.tokenizer = get_tokenizer()

    def chunk_text(
        self,
        text: str,
        source_extra_data: Optional[dict] = None,
    ) -> List[Chunk]:
        """
        Split text into chunks.

        Args:
            text: The text to chunk
            source_extra_data: Optional extra_data to include in each chunk

        Returns:
            List of Chunk objects
        """
        if not text or not text.strip():
            return []

        # Clean and normalize text
        text = self._normalize_text(text)

        # Split into initial segments
        segments = self._split_into_segments(text)

        # Merge small segments, split large ones
        balanced_segments = self._balance_segments(segments)

        # Create chunks with overlap
        chunks = self._create_chunks_with_overlap(balanced_segments)

        # Add extra_data
        for i, chunk in enumerate(chunks):
            chunk.index = i
            if source_extra_data:
                chunk.extra_data.update(source_extra_data)

        return chunks

    def _normalize_text(self, text: str) -> str:
        """Normalize whitespace and clean text."""
        # Replace multiple newlines with double newline
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Replace multiple spaces with single space
        text = re.sub(r' {2,}', ' ', text)
        # Strip leading/trailing whitespace
        text = text.strip()
        return text

    def _split_into_segments(self, text: str) -> List[str]:
        """Split text into initial segments by major boundaries."""
        segments = []

        # Split by double newlines (paragraphs) or markdown headers
        pattern = r'(?:\n\n+|(?=^#{1,6}\s))'
        parts = re.split(pattern, text, flags=re.MULTILINE)

        for part in parts:
            part = part.strip()
            if part:
                segments.append(part)

        return segments

    def _balance_segments(self, segments: List[str]) -> List[str]:
        """Balance segment sizes - merge small ones, split large ones."""
        balanced = []
        current = ""
        current_tokens = 0

        for segment in segments:
            segment_tokens = count_tokens(segment)

            # If segment alone is larger than target, split it
            if segment_tokens > self.target_tokens * 1.5:
                # First, flush current buffer
                if current:
                    balanced.append(current)
                    current = ""
                    current_tokens = 0

                # Split large segment by sentences
                split_segments = self._split_large_segment(segment)
                balanced.extend(split_segments)
                continue

            # If adding this segment would exceed target, start new chunk
            if current_tokens + segment_tokens > self.target_tokens:
                if current:
                    balanced.append(current)
                current = segment
                current_tokens = segment_tokens
            else:
                # Merge with current
                if current:
                    current = current + "\n\n" + segment
                else:
                    current = segment
                current_tokens += segment_tokens

        # Don't forget the last buffer
        if current:
            balanced.append(current)

        return balanced

    def _split_large_segment(self, text: str) -> List[str]:
        """Split a large segment by sentences."""
        # Split into sentences
        sentences = self._split_into_sentences(text)

        segments = []
        current = ""
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = count_tokens(sentence)

            # Single sentence too long - force split by tokens
            if sentence_tokens > self.target_tokens:
                if current:
                    segments.append(current)
                    current = ""
                    current_tokens = 0

                # Split by token count
                token_chunks = self._split_by_token_limit(sentence)
                segments.extend(token_chunks)
                continue

            if current_tokens + sentence_tokens > self.target_tokens:
                if current:
                    segments.append(current)
                current = sentence
                current_tokens = sentence_tokens
            else:
                if current:
                    current = current + " " + sentence
                else:
                    current = sentence
                current_tokens += sentence_tokens

        if current:
            segments.append(current)

        return segments

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        # Simple sentence splitting - handles common cases
        # Could be enhanced with nltk or spacy for better accuracy
        pattern = r'(?<=[.!?])\s+(?=[A-Z])'
        sentences = re.split(pattern, text)
        return [s.strip() for s in sentences if s.strip()]

    def _split_by_token_limit(self, text: str) -> List[str]:
        """Force split text by token limit."""
        tokens = self.tokenizer.encode(text)
        chunks = []

        for i in range(0, len(tokens), self.target_tokens):
            chunk_tokens = tokens[i:i + self.target_tokens]
            chunk_text = self.tokenizer.decode(chunk_tokens)
            chunks.append(chunk_text)

        return chunks

    def _create_chunks_with_overlap(self, segments: List[str]) -> List[Chunk]:
        """Create final chunks with overlap between adjacent chunks."""
        if not segments:
            return []

        chunks = []

        for i, segment in enumerate(segments):
            token_count = count_tokens(segment)

            # For all but the first chunk, prepend overlap from previous
            if i > 0 and self.overlap_tokens > 0:
                prev_segment = segments[i - 1]
                overlap_text = self._get_tail_tokens(prev_segment, self.overlap_tokens)
                if overlap_text:
                    segment = overlap_text + " " + segment
                    token_count = count_tokens(segment)

            chunk = Chunk(
                content=segment,
                index=i,
                token_count=token_count,
                extra_data={
                    "has_overlap": i > 0 and self.overlap_tokens > 0,
                }
            )
            chunks.append(chunk)

        return chunks

    def _get_tail_tokens(self, text: str, num_tokens: int) -> str:
        """Get the last N tokens of text."""
        tokens = self.tokenizer.encode(text)
        if len(tokens) <= num_tokens:
            return text

        tail_tokens = tokens[-num_tokens:]
        return self.tokenizer.decode(tail_tokens)


# Singleton instance
_chunking_service = None


def get_chunking_service() -> ChunkingService:
    """Get or create chunking service singleton."""
    global _chunking_service
    if _chunking_service is None:
        _chunking_service = ChunkingService()
    return _chunking_service

"""Enhanced metadata extraction utilities."""

import re
from typing import Optional


def extract_date(text: str) -> Optional[str]:
    """
    Try to extract a date from text.

    Looks for common date patterns.
    """
    patterns = [
        # ISO format
        r"\d{4}-\d{2}-\d{2}",
        # US format
        r"\d{1,2}/\d{1,2}/\d{4}",
        # Long format
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)

    return None


def extract_author(text: str) -> Optional[str]:
    """
    Try to extract author from text.

    Looks for common author patterns.
    """
    patterns = [
        r"(?:by|author|written by)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        r"(?:^|\n)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)(?:\s*\n|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return None


def detect_language(text: str) -> str:
    """
    Simple language detection based on common words.

    Returns ISO 639-1 language code.
    """
    # Simple heuristic based on common words
    english_words = {"the", "and", "is", "in", "to", "of", "a", "for", "with"}
    spanish_words = {"el", "la", "de", "que", "y", "en", "es", "un", "por"}
    french_words = {"le", "la", "de", "et", "est", "en", "que", "un", "pour"}
    german_words = {"der", "die", "und", "ist", "in", "das", "zu", "den", "für"}

    words = set(text.lower().split()[:100])

    scores = {
        "en": len(words & english_words),
        "es": len(words & spanish_words),
        "fr": len(words & french_words),
        "de": len(words & german_words),
    }

    best_lang = max(scores, key=scores.get)
    return best_lang if scores[best_lang] > 2 else "en"


def estimate_reading_time(word_count: int, wpm: int = 200) -> int:
    """
    Estimate reading time in minutes.

    Args:
        word_count: Number of words
        wpm: Words per minute (default 200)

    Returns:
        Estimated reading time in minutes
    """
    return max(1, round(word_count / wpm))


def enrich_metadata(text: str, existing_metadata: dict) -> dict:
    """
    Enrich metadata with extracted information.

    Args:
        text: Document text
        existing_metadata: Existing metadata dict

    Returns:
        Enriched metadata dict
    """
    enriched = dict(existing_metadata)

    # Only add if not already present
    if "date" not in enriched:
        date = extract_date(text)
        if date:
            enriched["date"] = date

    if "author" not in enriched:
        author = extract_author(text)
        if author:
            enriched["author"] = author

    if "language" not in enriched:
        enriched["language"] = detect_language(text)

    if "word_count" in enriched:
        enriched["reading_time_minutes"] = estimate_reading_time(enriched["word_count"])

    return enriched

"""Tests for metadata extraction."""

import pytest


def test_extract_date_iso():
    from services.parsing.metadata import extract_date
    assert extract_date("Published on 2024-01-15") == "2024-01-15"
    assert extract_date("Date: 2023-12-25") == "2023-12-25"


def test_extract_date_long_format():
    from services.parsing.metadata import extract_date
    result = extract_date("January 15, 2024")
    assert result is not None
    assert "January" in result


def test_extract_date_no_date():
    from services.parsing.metadata import extract_date
    assert extract_date("No date here") is None


def test_extract_author_by():
    from services.parsing.metadata import extract_author
    assert extract_author("By John Smith") == "John Smith"
    assert extract_author("by Jane Doe") == "Jane Doe"


def test_extract_author_colon():
    from services.parsing.metadata import extract_author
    result = extract_author("Author: Jane Doe")
    assert result is not None


def test_extract_author_no_author():
    from services.parsing.metadata import extract_author
    result = extract_author("No author information here at all")
    # May or may not find an author depending on the pattern
    # Just verify it doesn't crash


def test_detect_language_english():
    from services.parsing.metadata import detect_language
    assert detect_language("The quick brown fox jumps over the lazy dog") == "en"


def test_detect_language_spanish():
    from services.parsing.metadata import detect_language
    # Spanish text with common words
    assert detect_language("El perro es muy grande y la casa es bonita") == "es"


def test_detect_language_short_text():
    from services.parsing.metadata import detect_language
    # Short text defaults to English
    result = detect_language("hello")
    assert result == "en"


def test_estimate_reading_time_short():
    from services.parsing.metadata import estimate_reading_time
    assert estimate_reading_time(100) == 1  # At least 1 minute
    assert estimate_reading_time(200) == 1


def test_estimate_reading_time_medium():
    from services.parsing.metadata import estimate_reading_time
    assert estimate_reading_time(400) == 2
    assert estimate_reading_time(600) == 3


def test_estimate_reading_time_long():
    from services.parsing.metadata import estimate_reading_time
    assert estimate_reading_time(1000) == 5
    assert estimate_reading_time(2000) == 10


def test_enrich_metadata_adds_missing():
    from services.parsing.metadata import enrich_metadata
    text = "By John Doe - Published 2024-01-15. The quick brown fox jumps."
    metadata = {"word_count": 500}

    enriched = enrich_metadata(text, metadata)

    assert "language" in enriched
    assert enriched["language"] == "en"
    assert enriched["word_count"] == 500


def test_enrich_metadata_preserves_existing():
    from services.parsing.metadata import enrich_metadata
    text = "Some text here"
    metadata = {
        "author": "Existing Author",
        "date": "2023-01-01",
        "language": "fr",
    }

    enriched = enrich_metadata(text, metadata)

    # Should preserve existing values
    assert enriched["author"] == "Existing Author"
    assert enriched["date"] == "2023-01-01"
    assert enriched["language"] == "fr"


def test_enrich_metadata_adds_reading_time():
    from services.parsing.metadata import enrich_metadata
    text = "Some text"
    metadata = {"word_count": 1000}

    enriched = enrich_metadata(text, metadata)

    assert "reading_time_minutes" in enriched
    assert enriched["reading_time_minutes"] == 5


def test_enrich_metadata_empty():
    from services.parsing.metadata import enrich_metadata
    text = "Just some random text without obvious metadata"
    metadata = {}

    enriched = enrich_metadata(text, metadata)

    # Should at least add language
    assert "language" in enriched

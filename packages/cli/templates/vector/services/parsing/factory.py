"""Parser factory and format detection."""

from typing import Optional, Type
import mimetypes

from .base import BaseParser, ParseResult
from .pdf import PDFParser
from .html import HTMLParser
from .markdown import MarkdownParser


# Register parsers
PARSERS: list[Type[BaseParser]] = [
    PDFParser,
    HTMLParser,
    MarkdownParser,
]


class PlainTextParser(BaseParser):
    """Fallback parser for plain text."""

    def can_parse(self, content_type: str, filename: Optional[str] = None) -> bool:
        return True  # Fallback, accepts anything

    def parse(self, content: bytes | str, filename: Optional[str] = None) -> ParseResult:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")

        text = self._clean_text(content)
        title = filename.rsplit(".", 1)[0] if filename else None

        return ParseResult(
            text=text,
            title=title,
            metadata={},
            sections=[],
            word_count=self._count_words(text),
        )


def detect_format(
    content_type: Optional[str] = None,
    filename: Optional[str] = None,
) -> str:
    """
    Detect document format from content type or filename.

    Returns:
        Format string: 'pdf', 'html', 'markdown', or 'text'
    """
    # Try content type first
    if content_type:
        content_type = content_type.lower().split(";")[0].strip()

        if "pdf" in content_type:
            return "pdf"
        if "html" in content_type:
            return "html"
        if "markdown" in content_type:
            return "markdown"

    # Try filename extension
    if filename:
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

        if ext == ".pdf":
            return "pdf"
        if ext in (".html", ".htm"):
            return "html"
        if ext in (".md", ".markdown", ".mdown"):
            return "markdown"

        # Use mimetypes as fallback
        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type:
            return detect_format(content_type=mime_type)

    return "text"


def get_parser(
    content_type: Optional[str] = None,
    filename: Optional[str] = None,
) -> BaseParser:
    """
    Get appropriate parser for content.

    Args:
        content_type: MIME type of content
        filename: Filename for extension-based detection

    Returns:
        Parser instance suitable for the content
    """
    # Normalize content type
    if content_type:
        content_type = content_type.lower().split(";")[0].strip()

    # Try each registered parser
    for parser_class in PARSERS:
        parser = parser_class()
        if parser.can_parse(content_type or "", filename):
            return parser

    # Fall back to plain text
    return PlainTextParser()


def parse_document(
    content: bytes | str,
    content_type: Optional[str] = None,
    filename: Optional[str] = None,
) -> ParseResult:
    """
    Parse document content using appropriate parser.

    Convenience function that combines get_parser and parse.
    """
    parser = get_parser(content_type, filename)
    return parser.parse(content, filename)

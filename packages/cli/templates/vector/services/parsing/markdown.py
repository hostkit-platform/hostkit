"""Markdown document parser."""

import re
from typing import Optional, List

import markdown
from bs4 import BeautifulSoup

from .base import BaseParser, ParseResult


class MarkdownParser(BaseParser):
    """
    Parser for Markdown documents.

    Converts Markdown to HTML, then extracts text while
    preserving document structure (headers, sections).
    """

    SUPPORTED_EXTENSIONS = {".md", ".markdown", ".mdown"}
    SUPPORTED_CONTENT_TYPES = {"text/markdown", "text/x-markdown"}

    def can_parse(self, content_type: str, filename: Optional[str] = None) -> bool:
        """Check if this parser can handle Markdown content."""
        if content_type.lower() in self.SUPPORTED_CONTENT_TYPES:
            return True
        if filename:
            ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            return ext in self.SUPPORTED_EXTENSIONS
        return False

    def parse(self, content: bytes | str, filename: Optional[str] = None) -> ParseResult:
        """
        Parse Markdown document.

        Extracts text and structure, identifying sections by headers.
        """
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")

        # Extract YAML frontmatter if present
        metadata, content = self._extract_frontmatter(content)

        # Extract sections directly from markdown
        sections = self._extract_sections(content)

        # Convert to HTML for clean text extraction
        md = markdown.Markdown(extensions=["extra", "meta", "toc"])
        html = md.convert(content)

        # Extract plain text from HTML
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")
        text = self._clean_text(text)

        # Get title from first H1 or frontmatter
        title = metadata.get("title")
        if not title:
            first_h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            if first_h1:
                title = first_h1.group(1).strip()
        if not title and filename:
            title = filename.rsplit(".", 1)[0]

        return ParseResult(
            text=text,
            title=title,
            metadata=metadata,
            sections=sections,
            word_count=self._count_words(text),
        )

    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        """Extract YAML frontmatter from markdown."""
        metadata = {}

        # Check for YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    metadata = yaml.safe_load(parts[1]) or {}
                    content = parts[2]
                except Exception:
                    pass  # Invalid YAML, ignore

        return metadata, content

    def _extract_sections(self, content: str) -> List[dict]:
        """Extract sections based on markdown headers."""
        sections = []
        current_section = {"type": "section", "heading": None, "level": 0, "content": []}

        lines = content.split("\n")

        for line in lines:
            # Check for headers
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)

            if header_match:
                # Save current section if it has content
                if current_section["content"]:
                    sections.append({
                        "type": "section",
                        "heading": current_section["heading"],
                        "level": current_section["level"],
                        "content": "\n".join(current_section["content"]).strip(),
                    })

                # Start new section
                level = len(header_match.group(1))
                heading = header_match.group(2).strip()
                current_section = {
                    "type": "section",
                    "heading": heading,
                    "level": level,
                    "content": [],
                }
            else:
                # Add line to current section
                if line.strip():
                    current_section["content"].append(line)

        # Don't forget the last section
        if current_section["content"]:
            sections.append({
                "type": "section",
                "heading": current_section["heading"],
                "level": current_section["level"],
                "content": "\n".join(current_section["content"]).strip(),
            })

        return sections

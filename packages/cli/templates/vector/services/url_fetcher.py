"""URL content fetching service."""

import httpx
from typing import Optional
from dataclasses import dataclass

from .parsing import HTMLParser


@dataclass
class FetchResult:
    """Result of URL fetch."""
    content: str
    content_type: str
    url: str  # Final URL after redirects
    title: Optional[str] = None


async def fetch_url(
    url: str,
    timeout: float = 30.0,
    max_size_mb: int = 50,
) -> FetchResult:
    """
    Fetch content from a URL.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        max_size_mb: Maximum content size in MB

    Returns:
        FetchResult with content and metadata

    Raises:
        ValueError: If URL is invalid or content too large
        httpx.HTTPError: If request fails
    """
    max_size = max_size_mb * 1024 * 1024

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        # First, do a HEAD request to check content length
        try:
            head_response = await client.head(url)
            content_length = head_response.headers.get("content-length")
            if content_length and int(content_length) > max_size:
                raise ValueError(f"Content too large: {content_length} bytes")
        except httpx.HTTPError:
            pass  # HEAD not supported, proceed with GET

        # Fetch content
        response = await client.get(url)
        response.raise_for_status()

        # Check actual content size
        if len(response.content) > max_size:
            raise ValueError(f"Content too large: {len(response.content)} bytes")

        content_type = response.headers.get("content-type", "text/plain")

        # Extract title if HTML
        title = None
        if "text/html" in content_type:
            parser = HTMLParser()
            result = parser.parse(response.text)
            title = result.title

        return FetchResult(
            content=response.text,
            content_type=content_type,
            url=str(response.url),
            title=title,
        )


def detect_content_type(content_type: str) -> str:
    """
    Detect document type from content-type header.

    Returns: 'html', 'pdf', 'text', 'markdown', or 'unknown'
    """
    content_type = content_type.lower()

    if "text/html" in content_type:
        return "html"
    elif "application/pdf" in content_type:
        return "pdf"
    elif "text/plain" in content_type:
        return "text"
    elif "text/markdown" in content_type:
        return "markdown"
    else:
        return "unknown"

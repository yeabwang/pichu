"""Structured types for web tools.

Provides typed result objects with citation support for reliable
LLM referencing and structured error handling.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

# Error Codes


class WebToolErrorCode(str, Enum):
    """Structured error codes matching modern LLM tool conventions."""

    # Budget errors
    MAX_SEARCHES_EXCEEDED = "max_searches_exceeded"
    MAX_FETCHES_EXCEEDED = "max_fetches_exceeded"
    MAX_BYTES_EXCEEDED = "max_bytes_exceeded"

    # Security errors
    DOMAIN_BLOCKED = "domain_blocked"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    URL_PROVENANCE_FAILED = "url_provenance_failed"
    PRIVATE_NETWORK_BLOCKED = "private_network_blocked"
    IP_ADDRESS_BLOCKED = "ip_address_blocked"
    LOCALHOST_BLOCKED = "localhost_blocked"
    INVALID_SCHEME = "invalid_scheme"

    # Fetch errors
    CONNECTION_FAILED = "connection_failed"
    TIMEOUT = "timeout"
    SSL_ERROR = "ssl_error"
    CONTENT_TOO_LARGE = "content_too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    HTTP_ERROR = "http_error"

    # Search errors
    NO_RESULTS = "no_results"
    RATE_LIMITED = "rate_limited"
    API_ERROR = "api_error"
    INVALID_QUERY = "invalid_query"


@dataclass
class WebToolError:
    """Structured error for web tools."""

    code: WebToolErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


# Citation System


@dataclass
class Citation:
    """A citation reference for LLM responses.

    Supports character-level citation precision.
    """

    id: int  # [1], [2], etc.
    url: str
    title: str | None = None
    char_start: int | None = None  # For fetch: character position
    char_end: int | None = None
    excerpt: str | None = None  # The cited text snippet

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "excerpt": self.excerpt,
        }

    def to_markdown_ref(self) -> str:
        """Return markdown citation reference like [1]."""
        return f"[{self.id}]"

    def to_markdown_footnote(self) -> str:
        """Return markdown footnote like [1]: url (title)."""
        if self.title:
            return f"[{self.id}]: {self.url} ({self.title})"
        return f"[{self.id}]: {self.url}"


# Search Results


@dataclass
class SearchResultItem:
    """A single search result with citation support."""

    title: str
    url: str
    snippet: str
    citation_id: int = 0  # Assigned during result creation
    page_age: str | None = None  # "2 days ago", "1 week ago"
    domain: str | None = None  # Extracted from URL
    position: int = 0  # 1-indexed position in results

    def __post_init__(self):
        if not self.domain and self.url:
            try:
                self.domain = urlparse(self.url).netloc
            except Exception:
                self.domain = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "citation_id": self.citation_id,
            "page_age": self.page_age,
            "domain": self.domain,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchResultItem":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            snippet=data.get("snippet", ""),
            citation_id=data.get("citation_id", 0),
            page_age=data.get("page_age"),
            domain=data.get("domain"),
            position=data.get("position", 0),
        )

    def to_citation(self) -> Citation:
        """Convert to a Citation object."""
        return Citation(
            id=self.citation_id,
            url=self.url,
            title=self.title,
            excerpt=self.snippet[:200] if self.snippet else None,
        )


@dataclass
class WebSearchResult:
    """Structured search result following common LLM patterns."""

    query: str
    results: list[SearchResultItem]
    backend: str  # "serper", "ddgs"
    cached: bool = False
    mode: str = "live"  # "live", "cached", "offline"
    total_results: int | None = None  # Total available (not just returned)
    search_time_ms: float | None = None
    timestamp: float = field(default_factory=time.time)
    error: WebToolError | None = None

    def __post_init__(self):
        # Assign citation IDs if not set
        for i, result in enumerate(self.results, 1):
            if result.citation_id == 0:
                result.citation_id = i
            if result.position == 0:
                result.position = i

    @property
    def success(self) -> bool:
        return self.error is None and len(self.results) > 0

    @property
    def result_count(self) -> int:
        return len(self.results)

    def get_urls(self) -> list[str]:
        """Get all URLs from results."""
        return [r.url for r in self.results]

    def get_citations(self) -> list[Citation]:
        """Get all citations from results."""
        return [r.to_citation() for r in self.results]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "backend": self.backend,
            "cached": self.cached,
            "mode": self.mode,
            "total_results": self.total_results,
            "result_count": self.result_count,
            "search_time_ms": self.search_time_ms,
            "timestamp": self.timestamp,
            "error": self.error.to_dict() if self.error else None,
        }

    def to_cited_text(self) -> str:
        """Format results with citation markers for LLM consumption.

        Returns:
            Formatted text with [1], [2] markers and footnotes.
        """
        if not self.results:
            return f'No results found for "{self.query}"'

        lines = []
        cache_indicator = " [cached]" if self.cached else ""

        lines.append(f'Found {self.result_count} results for "{self.query}" (via {self.backend}){cache_indicator}:')
        lines.append("")

        for result in self.results:
            lines.append(f"[{result.citation_id}] **{result.title}**")
            lines.append(f"    {result.url}")
            if result.snippet:
                snippet = result.snippet[:500]
                if len(result.snippet) > 500:
                    snippet += "..."
                lines.append(f"    {snippet}")
            if result.page_age:
                lines.append(f"    _{result.page_age}_")
            lines.append("")

        # Add citation footnotes
        lines.append("---")
        lines.append("**Sources:**")
        for result in self.results:
            lines.append(result.to_citation().to_markdown_footnote())

        return "\n".join(lines)

    def to_llm_text(self) -> str:
        """Simplified format for LLM context (less verbose)."""
        if not self.results:
            return f'No results for "{self.query}"'

        lines = [f'Results for "{self.query}":']
        for r in self.results:
            lines.append(f"[{r.citation_id}] {r.title} - {r.url}")
            if r.snippet:
                lines.append(f"    {r.snippet[:300]}")
        return "\n".join(lines)


# Fetched Documents


class DocumentType(str, Enum):
    """Supported document types."""

    HTML = "text/html"
    PDF = "application/pdf"
    PLAIN_TEXT = "text/plain"
    MARKDOWN = "text/markdown"
    JSON = "application/json"
    XML = "application/xml"
    UNKNOWN = "application/octet-stream"


@dataclass
class ContentChunk:
    """A chunk of content with citation info.

    Used for character-level citations.
    """

    text: str
    char_start: int
    char_end: int
    citation_id: int | None = None

    @property
    def length(self) -> int:
        return len(self.text)

    def to_citation(self, url: str, title: str | None = None) -> Citation:
        """Create a citation for this chunk."""
        return Citation(
            id=self.citation_id or 0,
            url=url,
            title=title,
            char_start=self.char_start,
            char_end=self.char_end,
            excerpt=self.text[:200] if len(self.text) > 200 else self.text,
        )


@dataclass
class FetchedDocument:
    """Structured document from web_fetch following common tool patterns."""

    url: str
    content: str  # Full content (markdown for HTML, text for others)
    mime_type: str
    title: str | None = None
    cached: bool = False
    mode: str = "live"
    char_count: int = 0
    byte_count: int = 0
    fetched_at: float = field(default_factory=time.time)
    truncated: bool = False
    original_length: int | None = None
    error: WebToolError | None = None

    # For PDF and multi-page documents
    page_count: int | None = None
    current_page: int | None = None

    # Chunked content for citation support
    _chunks: list[ContentChunk] = field(default_factory=list, repr=False)

    def __post_init__(self):
        if not self.char_count:
            self.char_count = len(self.content)
        if not self.byte_count:
            self.byte_count = len(self.content.encode("utf-8", errors="replace"))

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.content)

    @property
    def document_type(self) -> DocumentType:
        """Get the document type enum."""
        for dt in DocumentType:
            if dt.value in self.mime_type:
                return dt
        return DocumentType.UNKNOWN

    @property
    def domain(self) -> str:
        """Extract domain from URL."""
        try:
            return urlparse(self.url).netloc
        except Exception:
            return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "mime_type": self.mime_type,
            "document_type": self.document_type.value,
            "char_count": self.char_count,
            "byte_count": self.byte_count,
            "cached": self.cached,
            "mode": self.mode,
            "truncated": self.truncated,
            "page_count": self.page_count,
            "fetched_at": self.fetched_at,
            "error": self.error.to_dict() if self.error else None,
        }

    def get_chunk(self, char_start: int, char_end: int) -> ContentChunk:
        """Get a content chunk for a character range."""
        text = self.content[char_start:char_end]
        return ContentChunk(
            text=text,
            char_start=char_start,
            char_end=char_end,
        )

    def create_citation(self, char_start: int, char_end: int, citation_id: int = 1) -> Citation:
        """Create a citation for a specific text range."""
        excerpt = self.content[char_start:char_end]
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "..."

        return Citation(
            id=citation_id,
            url=self.url,
            title=self.title,
            char_start=char_start,
            char_end=char_end,
            excerpt=excerpt,
        )

    def find_text(self, pattern: str) -> list[ContentChunk]:
        """Find all occurrences of pattern and return as chunks."""
        chunks = []
        content_lower = self.content.lower()
        pattern_lower = pattern.lower()

        start = 0
        citation_id = 1
        while True:
            pos = content_lower.find(pattern_lower, start)
            if pos == -1:
                break

            # Get context around the match
            context_start = max(0, pos - 50)
            context_end = min(len(self.content), pos + len(pattern) + 50)

            chunk = ContentChunk(
                text=self.content[context_start:context_end],
                char_start=context_start,
                char_end=context_end,
                citation_id=citation_id,
            )
            chunks.append(chunk)
            citation_id += 1
            start = pos + 1

        return chunks

    def to_cited_text(self) -> str:
        """Format document content with header and citation info."""
        lines = []
        cache_indicator = " [cached]" if self.cached else ""

        # Header
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")

        lines.append(f"**Source:** {self.url}{cache_indicator}")
        lines.append(f"**Type:** {self.document_type.value}")
        lines.append(f"**Length:** {self.char_count:,} characters")
        if self.truncated:
            lines.append(f"**Note:** Content truncated from {self.original_length:,} characters")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Content
        lines.append(self.content)

        # Footer citation
        lines.append("")
        lines.append("---")
        lines.append(f"[source]: {self.url}")

        return "\n".join(lines)

    def to_llm_text(self) -> str:
        """Simplified format for LLM context."""
        header = f"Content from {self.url}"
        if self.title:
            header = f"{self.title} ({self.url})"
        if self.cached:
            header += " [cached]"
        return f"{header}\n\n{self.content}"


# Find Results (for in-page search)


@dataclass
class FindMatch:
    """A single match from in-page search."""

    line_number: int
    char_start: int
    char_end: int
    context: str  # Text with surrounding context
    citation_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "context": self.context,
            "citation_id": self.citation_id,
        }

    def to_citation(self, url: str, title: str | None = None) -> Citation:
        return Citation(
            id=self.citation_id,
            url=url,
            title=title,
            char_start=self.char_start,
            char_end=self.char_end,
            excerpt=self.context,
        )


@dataclass
class FindResult:
    """Structured result from find-in-page operation."""

    url: str
    pattern: str
    matches: list[FindMatch]
    total_matches: int
    shown_matches: int
    cached: bool = False
    error: WebToolError | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def found(self) -> bool:
        return len(self.matches) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "pattern": self.pattern,
            "matches": [m.to_dict() for m in self.matches],
            "total_matches": self.total_matches,
            "shown_matches": self.shown_matches,
            "found": self.found,
            "cached": self.cached,
            "error": self.error.to_dict() if self.error else None,
        }

    def get_citations(self) -> list[Citation]:
        """Get citations for all matches."""
        return [m.to_citation(self.url) for m in self.matches]

    def to_cited_text(self) -> str:
        """Format find results with citations."""
        if not self.found:
            return f'Pattern "{self.pattern}" not found in {self.url}'

        lines = [
            f'Found {self.total_matches} occurrence(s) of "{self.pattern}" in {self.url}:',
            "",
        ]

        for match in self.matches:
            lines.append(f"**[{match.citation_id}] Line {match.line_number}:**")
            lines.append("```")
            lines.append(match.context)
            lines.append("```")
            lines.append("")

        if self.total_matches > self.shown_matches:
            lines.append(f"... and {self.total_matches - self.shown_matches} more occurrences")

        return "\n".join(lines)


# User Location (for search biasing)


@dataclass
class UserLocation:
    """User location for search result biasing."""

    country: str = "US"  # ISO 3166-1 alpha-2
    region: str | None = None  # State/province
    city: str | None = None
    timezone: str = "America/New_York"
    language: str = "en"

    def to_serper_params(self) -> dict[str, str]:
        """Convert to Serper API parameters."""
        params = {"gl": self.country.lower(), "hl": self.language}
        return params

    def to_ddgs_region(self) -> str:
        """Convert to ddgs region string."""
        # ddgs uses format like "us-en", "uk-en", etc.
        return f"{self.country.lower()}-{self.language}"


# Tool Budget


@dataclass
class ToolBudget:
    """Per-session budget for tool usage with max-use style limits."""

    # Search limits
    max_searches: int = 20
    max_results_per_search: int = 10

    # Fetch limits
    max_fetches: int = 30
    max_bytes_total: int = 20_000_000  # 20MB total

    # Current usage
    searches_used: int = 0
    fetches_used: int = 0
    bytes_fetched: int = 0
    results_returned: int = 0

    def can_search(self) -> tuple[bool, WebToolError | None]:
        """Check if a search operation is allowed."""
        if self.searches_used >= self.max_searches:
            return False, WebToolError(
                code=WebToolErrorCode.MAX_SEARCHES_EXCEEDED,
                message=f"Maximum searches ({self.max_searches}) exceeded",
                details={"used": self.searches_used, "max": self.max_searches},
            )
        return True, None

    def can_fetch(self, estimated_bytes: int = 0) -> tuple[bool, WebToolError | None]:
        """Check if a fetch operation is allowed."""
        if self.fetches_used >= self.max_fetches:
            return False, WebToolError(
                code=WebToolErrorCode.MAX_FETCHES_EXCEEDED,
                message=f"Maximum fetches ({self.max_fetches}) exceeded",
                details={"used": self.fetches_used, "max": self.max_fetches},
            )

        if self.bytes_fetched + estimated_bytes > self.max_bytes_total:
            return False, WebToolError(
                code=WebToolErrorCode.MAX_BYTES_EXCEEDED,
                message=f"Maximum bytes ({self.max_bytes_total:,}) would be exceeded",
                details={
                    "used": self.bytes_fetched,
                    "max": self.max_bytes_total,
                    "requested": estimated_bytes,
                },
            )

        return True, None

    def record_search(self, result_count: int = 0) -> None:
        """Record a search operation."""
        self.searches_used += 1
        self.results_returned += result_count

    def record_fetch(self, byte_count: int = 0) -> None:
        """Record a fetch operation."""
        self.fetches_used += 1
        self.bytes_fetched += byte_count

    def get_usage_summary(self) -> dict[str, Any]:
        """Get usage summary for display."""
        return {
            "searches": f"{self.searches_used}/{self.max_searches}",
            "fetches": f"{self.fetches_used}/{self.max_fetches}",
            "bytes": f"{self.bytes_fetched:,}/{self.max_bytes_total:,}",
            "results": self.results_returned,
        }

    def reset(self) -> None:
        """Reset usage counters."""
        self.searches_used = 0
        self.fetches_used = 0
        self.bytes_fetched = 0
        self.results_returned = 0

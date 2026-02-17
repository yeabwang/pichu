"""web fetch tool with structured outputs, security, and PDF support.

Features:
- Structured document results with citations
- URL provenance validation (anti-exfiltration)
- Domain allow/block lists
- Per-session budgets
- PDF extraction support
- Playwright (primary) with httpx fallback
- SQLite-based persistent cache
- Rate limiting and retry
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, Field

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from utils.cache import (
    CacheMode,
    MultiRateLimiter,
    RetryError,
    WebCache,
    retry_async,
)
from utils.cache import (
    RetryConfig as CacheRetryConfig,
)
from utils.web_security import WebSecurityManager
from utils.web_types import (
    FetchedDocument,
    FindMatch,
    FindResult,
    ToolBudget,
    WebToolError,
    WebToolErrorCode,
)

if TYPE_CHECKING:
    from config import Config


class WebFetchParams(BaseModel):
    """Parameters for web fetch tool."""

    action: Literal["fetch", "find"] = Field(
        default="fetch",
        description=(
            "Action to perform: 'fetch' retrieves URL content as markdown, "
            "'find' searches for a pattern within the page content"
        ),
    )
    url: str = Field(
        ...,
        description="The URL to fetch content from",
    )
    pattern: str | None = Field(
        default=None,
        description="For 'find' action: text pattern to search within the page",
    )
    mode: Literal["live", "cached", "offline"] = Field(
        default="cached",
        description=(
            "Cache mode: 'live' always fetches fresh, "
            "'cached' returns cache if valid else fetches, "
            "'offline' only returns cached content"
        ),
    )


class WebFetchTool(Tool):
    """Fetch URL content with structured, citable results.

    Uses Playwright (primary) with httpx fallback.
    Returns structured documents with citation support.
    Validates URL provenance to prevent exfiltration.
    Supports PDF extraction.
    """

    name = "web_fetch"
    description = (
        "Fetch content from a URL and convert to readable markdown. "
        "Returns structured document with citation support. "
        "Supports JavaScript-rendered pages via Playwright and PDF extraction. "
        "Actions: 'fetch' to retrieve content, 'find' to search within page. "
        "Note: Can only fetch URLs from search results or user-provided URLs. "
        "Example: fetch 'https://docs.python.org/3/library/asyncio.html'"
    )
    kind = ToolKind.NETWORK
    schema = WebFetchParams

    def __init__(
        self,
        config: "Config | None" = None,
        security_manager: WebSecurityManager | None = None,
        budget: ToolBudget | None = None,
    ) -> None:
        super().__init__(config)
        self._fetch_config = config.web_fetch if config and hasattr(config, "web_fetch") else None
        self._search_config = config.web_search if config and hasattr(config, "web_search") else None

        self._playwright_available: bool | None = None
        self._cache: WebCache | None = None
        self._rate_limiter: MultiRateLimiter | None = None

        # Shared security manager and budget (from session or web_search)
        self._security = security_manager
        self._budget = budget

        self._init_components()

    def _init_components(self) -> None:
        """Initialize cache, rate limiter, security, and budget from config."""
        if not self._search_config:
            return

        # Cache
        cache_cfg = self._search_config.cache
        if cache_cfg.enabled:
            cache_dir = cache_cfg.cache_dir
            if self._config and hasattr(self._config, "cwd"):
                cache_dir = self._config.cwd / cache_cfg.cache_dir
            self._cache = WebCache(
                cache_dir=cache_dir,
                search_ttl_hours=cache_cfg.search_ttl_hours,
                fetch_ttl_hours=cache_cfg.fetch_ttl_hours,
                max_size_mb=cache_cfg.max_size_mb,
            )

        # Rate limiter
        rate_cfg = self._search_config.rate_limit
        if rate_cfg.enabled:
            self._rate_limiter = MultiRateLimiter(
                serper_rpm=rate_cfg.serper_rpm,
                ddgs_rpm=rate_cfg.ddgs_rpm,
                fetch_rpm=rate_cfg.fetch_rpm,
            )

        # Security (if not provided externally)
        if self._security is None and hasattr(self._search_config, "security"):
            from utils.web_security import WebSecurityConfig as SecConfig

            sec_cfg = self._search_config.security
            security_config = SecConfig(
                allowed_domains=sec_cfg.allowed_domains,
                blocked_domains=sec_cfg.blocked_domains,
                allow_ip_addresses=sec_cfg.allow_ip_addresses,
                allow_localhost=sec_cfg.allow_localhost,
                allow_private_networks=sec_cfg.allow_private_networks,
                allowed_schemes=sec_cfg.allowed_schemes,
                require_url_provenance=sec_cfg.require_url_provenance,
            )
            self._security = WebSecurityManager(security_config)

        # Budget (if not provided externally)
        if self._budget is None and hasattr(self._search_config, "budget"):
            budget_cfg = self._search_config.budget
            self._budget = ToolBudget(
                max_searches=budget_cfg.max_searches,
                max_results_per_search=budget_cfg.max_results_per_search,
                max_fetches=budget_cfg.max_fetches,
                max_bytes_total=budget_cfg.max_bytes_total,
            )

    def set_security_manager(self, manager: WebSecurityManager) -> None:
        """Set shared security manager (for session-level provenance)."""
        self._security = manager

    def set_budget(self, budget: ToolBudget) -> None:
        """Set shared budget (for session-level tracking)."""
        self._budget = budget

    @property
    def _max_content_length(self) -> int:
        if self._fetch_config:
            return self._fetch_config.max_content_length
        return 50000

    @property
    def _httpx_timeout(self) -> int:
        if self._fetch_config:
            return self._fetch_config.httpx_timeout
        return 30

    @property
    def _playwright_timeout(self) -> int:
        if self._fetch_config:
            return self._fetch_config.playwright_timeout
        return 10000

    @property
    def _user_agent(self) -> str:
        if self._fetch_config:
            return self._fetch_config.user_agent
        return "pichu/1.0"

    @property
    def _verify_ssl(self) -> bool:
        if self._fetch_config:
            return self._fetch_config.verify_ssl
        return True

    @property
    def _use_playwright(self) -> bool:
        if self._fetch_config:
            return self._fetch_config.use_playwright
        return True

    @property
    def _find_context_lines(self) -> int:
        if self._fetch_config:
            return self._fetch_config.find_context_lines
        return 2

    @property
    def _find_max_matches(self) -> int:
        if self._fetch_config:
            return self._fetch_config.find_max_matches
        return 10

    @property
    def _pdf_enabled(self) -> bool:
        if self._fetch_config and hasattr(self._fetch_config, "pdf_enabled"):
            return self._fetch_config.pdf_enabled
        return True

    @property
    def _pdf_max_pages(self) -> int:
        if self._fetch_config and hasattr(self._fetch_config, "pdf_max_pages"):
            return self._fetch_config.pdf_max_pages
        return 50

    @property
    def _retry_config(self) -> CacheRetryConfig:
        if self._search_config and self._search_config.retry:
            cfg = self._search_config.retry
            return CacheRetryConfig(
                max_attempts=cfg.max_attempts,
                base_delay=cfg.base_delay,
                max_delay=cfg.max_delay,
                exponential_base=cfg.exponential_base,
                jitter=cfg.jitter,
            )
        return CacheRetryConfig()

    def _get_cache_mode(self, mode_str: str) -> CacheMode:
        """Convert string to CacheMode enum."""
        mode_map = {
            "live": CacheMode.LIVE,
            "cached": CacheMode.CACHED,
            "offline": CacheMode.OFFLINE,
        }
        return mode_map.get(mode_str, CacheMode.CACHED)

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        params = WebFetchParams(**invocation.params)
        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"Fetch URL: {params.url}",
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute web fetch or find with structured output."""
        params = WebFetchParams(**invocation.params)
        mode = self._get_cache_mode(params.mode)

        # Security check: validate URL
        if self._security:
            allowed, security_error = self._security.check_url(params.url)
            if not allowed and security_error:
                return self._create_error_result(security_error, params.url)

        # Budget check
        if self._budget:
            can_fetch, budget_error = self._budget.can_fetch()
            if not can_fetch and budget_error:
                return ToolResult.error_result(
                    budget_error.message,
                    metadata={"error_code": budget_error.code.value, "details": budget_error.details},
                )

        if params.action == "fetch":
            result = await self._fetch(params.url, mode)
        elif params.action == "find":
            if not params.pattern:
                return ToolResult.error_result("'find' action requires a 'pattern' parameter")
            result = await self._find(params.url, params.pattern, mode)
        else:
            return ToolResult.error_result(f"Unknown action: {params.action}")

        # Record budget usage
        if self._budget and result.success:
            byte_count = result.metadata.get("byte_count", 0)
            self._budget.record_fetch(byte_count)

        return result

    async def _fetch(self, url: str, mode: CacheMode) -> ToolResult:
        """Fetch URL content and return structured document."""
        time.time()

        # Validate URL format
        if not url.startswith(("http://", "https://")):
            error = WebToolError(
                code=WebToolErrorCode.HTTP_ERROR,
                message=f"Invalid URL: {url}",
                details={"hint": "URL must start with http:// or https://"},
            )
            return self._create_error_result(error, url)

        # Check cache first
        if self._cache and mode in (CacheMode.CACHED, CacheMode.OFFLINE):
            cached = self._cache.get_fetch(url)
            if cached:
                doc = FetchedDocument(
                    url=url,
                    content=cached["content"],
                    mime_type=cached.get("mime_type", "text/html"),
                    title=cached.get("title"),
                    cached=True,
                    mode=mode.value,
                    char_count=cached.get("content_length", len(cached["content"])),
                )
                return self._create_fetch_success(doc)

            if mode == CacheMode.OFFLINE:
                error = WebToolError(
                    code=WebToolErrorCode.NO_RESULTS,
                    message=f'No cached content for "{url}"',
                    details={"hint": 'Use mode="cached" or "live" to fetch'},
                )
                return self._create_error_result(error, url)

        # Rate limit
        if self._rate_limiter:
            if not self._rate_limiter.acquire("fetch", timeout=10.0):
                error = WebToolError(
                    code=WebToolErrorCode.RATE_LIMITED,
                    message="Rate limit exceeded for URL fetching",
                )
                return self._create_error_result(error, url)

        # Determine content type and fetch
        content = None
        mime_type = None
        title = None
        raw_content = None

        # Try Playwright first for HTML
        if self._use_playwright and self._check_playwright_available():
            try:
                pw_result = await self._fetch_with_retry_playwright(url)
                raw_content, mime_type, title = pw_result
                content = raw_content
            except Exception:
                content = None

        # Fallback to httpx
        if content is None:
            try:
                httpx_result = await self._fetch_with_retry_httpx(url)
                raw_content, mime_type = httpx_result
                content = raw_content
            except Exception as e:
                error = WebToolError(
                    code=WebToolErrorCode.CONNECTION_FAILED,
                    message=f"Failed to fetch {url}",
                    details={"error": str(e)},
                )
                return self._create_error_result(error, url)

        if not content:
            error = WebToolError(
                code=WebToolErrorCode.NO_RESULTS,
                message=f"Failed to retrieve content from {url}",
            )
            return self._create_error_result(error, url)

        # Handle different content types
        if mime_type and "application/pdf" in mime_type:
            if self._pdf_enabled:
                try:
                    content, title = await self._extract_pdf(raw_content or "")
                except Exception as e:
                    error = WebToolError(
                        code=WebToolErrorCode.UNSUPPORTED_CONTENT_TYPE,
                        message=f"Failed to extract PDF: {e}",
                    )
                    return self._create_error_result(error, url)
            else:
                error = WebToolError(
                    code=WebToolErrorCode.UNSUPPORTED_CONTENT_TYPE,
                    message="PDF extraction is disabled",
                )
                return self._create_error_result(error, url)
        elif mime_type and mime_type.startswith("text/html"):
            content, title = self._html_to_markdown(content)
        elif self._looks_like_html(content):
            content, title = self._html_to_markdown(content)

        # Truncate if needed
        truncated = False
        original_length = len(content)
        if len(content) > self._max_content_length:
            content = content[: self._max_content_length]
            content += f"\n\n[Content truncated at {self._max_content_length:,} characters]"
            truncated = True

        # Cache the result
        if self._cache and mode != CacheMode.OFFLINE:
            self._cache.set_fetch(
                url=url,
                content=content,
                title=title,
                mime_type=mime_type,
            )

        # Create structured document
        doc = FetchedDocument(
            url=url,
            content=content,
            mime_type=mime_type or "text/html",
            title=title,
            cached=False,
            mode=mode.value,
            char_count=len(content),
            byte_count=len(content.encode("utf-8", errors="replace")),
            truncated=truncated,
            original_length=original_length if truncated else None,
        )

        return self._create_fetch_success(doc)

    async def _find(self, url: str, pattern: str, mode: CacheMode) -> ToolResult:
        """Fetch URL and search for pattern with structured results."""
        # First fetch the content
        fetch_result = await self._fetch(url, mode)
        if not fetch_result.success:
            return fetch_result

        # Get content from metadata or output
        content = fetch_result.output or ""

        # Remove the header for searching
        content_start = content.find("\n---\n")
        if content_start > 0:
            content = content[content_start + 5 :]

        # Search for pattern (case-insensitive)
        pattern_lower = pattern.lower()
        content_lower = content.lower()

        if pattern_lower not in content_lower:
            find_result = FindResult(
                url=url,
                pattern=pattern,
                matches=[],
                total_matches=0,
                shown_matches=0,
                cached=fetch_result.metadata.get("cached", False),
            )
            return self._create_find_success(find_result)

        # Find all occurrences with context
        matches: list[FindMatch] = []
        lines = content.split("\n")
        context_lines = self._find_context_lines

        for i, line in enumerate(lines):
            if pattern_lower in line.lower():
                # Get context lines
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                context = "\n".join(lines[start:end])

                # Calculate character positions
                line_start = sum(len(line_text) + 1 for line_text in lines[:i])
                match_pos = line.lower().find(pattern_lower)

                matches.append(
                    FindMatch(
                        line_number=i + 1,
                        char_start=line_start + match_pos,
                        char_end=line_start + match_pos + len(pattern),
                        context=context,
                        citation_id=len(matches) + 1,
                    )
                )

        max_matches = self._find_max_matches
        find_result = FindResult(
            url=url,
            pattern=pattern,
            matches=matches[:max_matches],
            total_matches=len(matches),
            shown_matches=min(len(matches), max_matches),
            cached=fetch_result.metadata.get("cached", False),
        )

        return self._create_find_success(find_result)

    def _create_fetch_success(self, doc: FetchedDocument) -> ToolResult:
        """Create successful ToolResult from FetchedDocument."""
        output = doc.to_cited_text()

        return ToolResult.success_result(
            output=output,
            metadata={
                "url": doc.url,
                "title": doc.title,
                "mime_type": doc.mime_type,
                "document_type": doc.document_type.value,
                "char_count": doc.char_count,
                "byte_count": doc.byte_count,
                "cached": doc.cached,
                "mode": doc.mode,
                "truncated": doc.truncated,
            },
        )

    def _create_find_success(self, result: FindResult) -> ToolResult:
        """Create successful ToolResult from FindResult."""
        output = result.to_cited_text()

        return ToolResult.success_result(
            output=output,
            metadata={
                "url": result.url,
                "pattern": result.pattern,
                "found": result.found,
                "match_count": result.total_matches,
                "shown_matches": result.shown_matches,
                "cached": result.cached,
                "citations": [c.to_dict() for c in result.get_citations()],
            },
        )

    def _create_error_result(self, error: WebToolError, url: str) -> ToolResult:
        """Create error ToolResult."""
        return ToolResult.error_result(
            error.message,
            metadata={
                "url": url,
                "error_code": error.code.value,
                "details": error.details,
            },
        )

    def _check_playwright_available(self) -> bool:
        """Check if Playwright is available."""
        if self._playwright_available is not None:
            return self._playwright_available

        try:
            self._playwright_available = True
        except Exception:
            self._playwright_available = False

        return self._playwright_available

    async def _fetch_with_retry_playwright(self, url: str) -> tuple[str | None, str | None, str | None]:
        """Fetch URL using Playwright with retry."""
        try:
            return await retry_async(
                lambda: self._fetch_with_playwright(url),
                config=self._retry_config,
                retryable_exceptions=(Exception,),
            )
        except RetryError as e:
            raise Exception(f"Playwright fetch failed: {e.last_error}")

    async def _fetch_with_retry_httpx(self, url: str) -> tuple[str | None, str | None]:
        """Fetch URL using httpx with retry."""
        try:
            return await retry_async(
                lambda: self._fetch_with_httpx(url),
                config=self._retry_config,
                retryable_exceptions=(httpx.HTTPError, Exception),
            )
        except RetryError as e:
            raise Exception(f"HTTP fetch failed: {e.last_error}")

    async def _fetch_with_playwright(self, url: str) -> tuple[str | None, str | None, str | None]:
        """Fetch URL using Playwright for JS-rendered pages."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _sync_fetch():
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        ignore_https_errors=not self._verify_ssl,
                        user_agent=f"Mozilla/5.0 ({self._user_agent})",
                    )
                    page = context.new_page()

                    try:
                        response = page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=self._playwright_timeout,
                        )
                    except Exception:
                        response = page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=self._playwright_timeout,
                        )

                    content = page.content()
                    title = page.title()

                    mime_type = None
                    if response:
                        content_type = response.header_value("content-type")
                        if content_type:
                            mime_type = content_type.split(";")[0]

                    return content, mime_type, title
                finally:
                    browser.close()

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, _sync_fetch)

    async def _fetch_with_httpx(self, url: str) -> tuple[str | None, str | None]:
        """Fetch URL using httpx as fallback."""
        headers = {"User-Agent": f"Mozilla/5.0 ({self._user_agent})"}

        async with httpx.AsyncClient(
            headers=headers,
            verify=self._verify_ssl,
            follow_redirects=True,
            timeout=self._httpx_timeout,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            mime_type = content_type.split(";")[0] if content_type else None
            return response.text, mime_type

    async def _extract_pdf(self, content: str | bytes) -> tuple[str, str | None]:
        """Extract text from PDF content."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _sync_extract():
            try:
                from io import BytesIO

                import pypdf

                if isinstance(content, str):
                    pdf_bytes = content.encode("latin-1")
                else:
                    pdf_bytes = content

                reader = pypdf.PdfReader(BytesIO(pdf_bytes))

                title = None
                if reader.metadata and reader.metadata.title:
                    title = reader.metadata.title

                text_parts = []
                max_pages = min(len(reader.pages), self._pdf_max_pages)

                for i, page in enumerate(reader.pages[:max_pages]):
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

                if len(reader.pages) > max_pages:
                    text_parts.append(f"\n[Extracted {max_pages} of {len(reader.pages)} pages]")

                return "\n\n".join(text_parts), title

            except ImportError:
                raise Exception("pypdf not installed. Install with: pip install pypdf")

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, _sync_extract)

    def _looks_like_html(self, content: str) -> bool:
        """Check if content looks like HTML."""
        html_patterns = [
            r"<!DOCTYPE\s+html",
            r"<html",
            r"<head",
            r"<body",
            r"<div",
            r"<p>",
        ]
        return any(re.search(p, content[:1000], re.IGNORECASE) for p in html_patterns)

    def _html_to_markdown(self, html: str) -> tuple[str, str | None]:
        """Convert HTML to markdown, extracting title."""
        try:
            import html2text
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")

            # Extract title
            title = None
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)

            # Remove unwanted elements
            for tag in soup.find_all(
                [
                    "script",
                    "style",
                    "svg",
                    "noscript",
                    "iframe",
                    "nav",
                    "footer",
                    "aside",
                ]
            ):
                tag.decompose()

            # Remove data: URIs
            for tag in soup.find_all(href=lambda x: x and x.startswith("data:")):
                tag.decompose()
            for tag in soup.find_all(src=lambda x: x and x.startswith("data:")):
                tag.decompose()

            # Convert to markdown
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.ignore_emphasis = False
            h.body_width = 0
            h.skip_internal_links = True

            markdown = h.handle(str(soup))
            markdown = re.sub(r"\n{3,}", "\n\n", markdown)
            markdown = markdown.strip()

            return markdown, title

        except ImportError:
            # Fallback: basic tag stripping
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text.strip(), None

    def get_cache_stats(self) -> dict[str, Any] | None:
        """Get cache statistics."""
        if not self._cache:
            return None

        stats = self._cache.get_stats()
        return {
            "search_entries": stats.search_entries,
            "fetch_entries": stats.fetch_entries,
            "total_size_mb": stats.total_size_bytes / (1024 * 1024),
            "total_hits": stats.total_hits,
        }

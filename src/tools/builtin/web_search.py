"""web search tool with structured outputs, security, and budgets.

Features:
- Structured results with citations
- Domain allow/block lists
- URL provenance tracking
- Per-session budgets
- Serper API (primary) with ddgs fallback
- SQLite-based persistent cache
- Rate limiting and retry
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, Field
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
    SearchResultItem,
    ToolBudget,
    WebSearchResult,
    WebToolError,
    WebToolErrorCode,
)

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

if TYPE_CHECKING:
    from config import Config


class WebSearchParams(BaseModel):
    """Parameters for web search tool."""

    query: str = Field(
        ...,
        description="Search query to find information on the web",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of search results to return",
    )
    mode: Literal["live", "cached", "offline"] = Field(
        default="cached",
        description=(
            "Cache mode: 'live' always fetches fresh results, "
            "'cached' returns cache if valid else fetches, "
            "'offline' only returns cached results"
        ),
    )


class WebSearchTool(Tool):
    """Search the web for information with structured, citable results.

    Uses Serper API (primary) with ddgs as fallback.
    Returns structured results with citation IDs for reliable referencing.
    Supports security controls, budgets, caching, and rate limiting.
    """

    name = "web_search"
    description = (
        "Search the web for information using Google (via Serper) or DuckDuckGo. "
        "Returns structured search results with citation IDs [1], [2], etc. "
        "for reliable referencing. Supports cache modes: 'live' (always fresh), "
        "'cached' (use cache if valid), 'offline' (only cached). "
        "Example: search for 'python async tutorial'"
    )
    kind = ToolKind.NETWORK
    schema = WebSearchParams

    def __init__(
        self,
        config: "Config | None" = None,
        security_manager: WebSecurityManager | None = None,
        budget: ToolBudget | None = None,
    ) -> None:
        super().__init__(config)
        self._search_config = config.web_search if config and hasattr(config, "web_search") else None

        # Initialize components
        self._cache: WebCache | None = None
        self._rate_limiter: MultiRateLimiter | None = None

        # Shared security manager and budget (from session or externally provided)
        self._security: WebSecurityManager | None = security_manager
        self._budget: ToolBudget | None = budget

        self._init_components()

    def set_security_manager(self, manager: WebSecurityManager) -> None:
        """Set shared security manager (for session-level provenance)."""
        self._security = manager

    def set_budget(self, budget: ToolBudget) -> None:
        """Set shared budget (for session-level tracking)."""
        self._budget = budget

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

        # Security (only create if not provided externally)
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

        # Budget (only create if not provided externally)
        if self._budget is None and hasattr(self._search_config, "budget"):
            budget_cfg = self._search_config.budget
            self._budget = ToolBudget(
                max_searches=budget_cfg.max_searches,
                max_results_per_search=budget_cfg.max_results_per_search,
                max_fetches=budget_cfg.max_fetches,
                max_bytes_total=budget_cfg.max_bytes_total,
            )

    @property
    def security_manager(self) -> WebSecurityManager | None:
        """Expose security manager for session-level provenance tracking."""
        return self._security

    @property
    def budget(self) -> ToolBudget | None:
        """Expose budget for session-level tracking."""
        return self._budget

    @property
    def _serper_api_key(self) -> str | None:
        if self._search_config and self._search_config.serper_api_key:
            return self._search_config.serper_api_key
        return os.environ.get("SERPER_API_KEY")

    @property
    def _max_results(self) -> int:
        if self._search_config:
            return self._search_config.max_results
        return 5

    @property
    def _region(self) -> str:
        if self._search_config:
            return self._search_config.region
        return "us-en"

    @property
    def _safesearch(self) -> str:
        if self._search_config:
            return self._search_config.safesearch
        return "moderate"

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
        params = WebSearchParams(**invocation.params)
        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"Web search: {params.query}",
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute web search with structured output."""
        params = WebSearchParams(**invocation.params)
        mode = self._get_cache_mode(params.mode)

        # Check budget
        if self._budget:
            can_search, budget_error = self._budget.can_search()
            if not can_search and budget_error:
                return ToolResult.error_result(
                    budget_error.message,
                    metadata={"error_code": budget_error.code.value, "details": budget_error.details},
                )

        # Perform search
        result = await self._search(params.query, params.max_results, mode)

        # Record budget usage
        if self._budget and result.success:
            result_count = result.metadata.get("result_count", 0)
            self._budget.record_search(result_count)

        return result

    async def _search(self, query: str, max_results: int, mode: CacheMode) -> ToolResult:
        """Perform web search with structured results."""
        start_time = time.time()
        backend_used = "serper"

        # Determine primary and fallback backends
        primary = "serper" if self._serper_api_key else "ddgs"
        fallback = "ddgs" if primary == "serper" else None

        # Check cache first (for cached and offline modes)
        if self._cache and mode in (CacheMode.CACHED, CacheMode.OFFLINE):
            cached = self._cache.get_search(query, primary, max_results)
            if cached:
                cached_results = [SearchResultItem.from_dict(r) for r in cached]

                # Register URLs with security manager
                if self._security:
                    self._security.register_search_results(cached_results)

                search_result = WebSearchResult(
                    query=query,
                    results=cached_results,
                    backend=primary,
                    cached=True,
                    mode=mode.value,
                    search_time_ms=(time.time() - start_time) * 1000,
                )

                return self._create_success_result(search_result)

            # Offline mode: fail if not in cache
            if mode == CacheMode.OFFLINE:
                error = WebToolError(
                    code=WebToolErrorCode.NO_RESULTS,
                    message=f'No cached results for "{query}"',
                    details={"hint": 'Use mode="cached" or "live" to fetch'},
                )
                return self._create_error_result(error, query)

        # Live fetch
        results: list[SearchResultItem] = []
        error_msg = None

        # Try primary backend (Serper)
        if primary == "serper" and self._serper_api_key:
            try:
                if self._rate_limiter:
                    if not self._rate_limiter.acquire("serper", timeout=10.0):
                        raise Exception("Rate limit exceeded for Serper API")

                results = await self._search_with_retry("serper", query, max_results)
                backend_used = "serper"
            except Exception as e:
                error_msg = str(e)

        # Try fallback backend (ddgs)
        if not results and fallback == "ddgs":
            try:
                if self._rate_limiter:
                    if not self._rate_limiter.acquire("ddgs", timeout=10.0):
                        raise Exception("Rate limit exceeded for DuckDuckGo")

                results = await self._search_with_retry("ddgs", query, max_results)
                backend_used = "ddgs"
            except Exception as e:
                if error_msg:
                    error_msg = f"Serper: {error_msg}, DuckDuckGo: {e}"
                else:
                    error_msg = str(e)

        # Try ddgs as primary if no Serper API key
        if not results and primary == "ddgs":
            try:
                if self._rate_limiter:
                    if not self._rate_limiter.acquire("ddgs", timeout=10.0):
                        raise Exception("Rate limit exceeded for DuckDuckGo")

                results = await self._search_with_retry("ddgs", query, max_results)
                backend_used = "ddgs"
            except Exception as e:
                error_msg = str(e)

        if not results:
            error = WebToolError(
                code=WebToolErrorCode.NO_RESULTS,
                message=f'Search failed for "{query}"',
                details={"error": error_msg or "No results found"},
            )
            return self._create_error_result(error, query)

        # Register URLs with security manager for provenance
        if self._security:
            self._security.register_search_results(results)

        # Cache the results
        if self._cache and mode != CacheMode.OFFLINE:
            self._cache.set_search(
                query=query,
                backend=backend_used,
                max_results=max_results,
                results=[r.to_dict() for r in results],
            )

        # Create structured result
        search_result = WebSearchResult(
            query=query,
            results=results,
            backend=backend_used,
            cached=False,
            mode=mode.value,
            search_time_ms=(time.time() - start_time) * 1000,
        )

        return self._create_success_result(search_result)

    def _create_success_result(self, search_result: WebSearchResult) -> ToolResult:
        """Create a successful ToolResult from WebSearchResult."""
        # Use cited text format for LLM
        output = search_result.to_cited_text()

        return ToolResult.success_result(
            output=output,
            metadata={
                "query": search_result.query,
                "backend": search_result.backend,
                "result_count": search_result.result_count,
                "cached": search_result.cached,
                "mode": search_result.mode,
                "search_time_ms": search_result.search_time_ms,
                "citations": [c.to_dict() for c in search_result.get_citations()],
                "urls": search_result.get_urls(),
            },
        )

    def _create_error_result(self, error: WebToolError, query: str) -> ToolResult:
        """Create an error ToolResult."""
        return ToolResult.error_result(
            error.message,
            metadata={
                "query": query,
                "error_code": error.code.value,
                "details": error.details,
            },
        )

    async def _search_with_retry(self, backend: str, query: str, max_results: int) -> list[SearchResultItem]:
        """Search with retry logic."""
        if backend == "serper":

            def search_func():
                return self._search_serper(query, max_results)
        else:

            def search_func():
                return self._search_ddgs(query, max_results)

        try:
            return await retry_async(
                search_func,
                config=self._retry_config,
                retryable_exceptions=(httpx.HTTPError, Exception),
            )
        except RetryError as e:
            raise Exception(f"Search failed after retries: {e.last_error}")

    async def _search_serper(self, query: str, max_results: int) -> list[SearchResultItem]:
        """Search using Serper API."""
        url = "https://google.serper.dev/search"
        if self._serper_api_key is None:
            raise ValueError("Serper API key is required for Serper backend")
        headers = {
            "X-API-KEY": self._serper_api_key,
            "Content-Type": "application/json",
        }

        # Build payload with region params from UserLocation
        region = self._region  # e.g., "us-en"
        region_parts = region.split("-")
        gl = region_parts[0] if region_parts else "us"  # Country code
        hl = region_parts[1] if len(region_parts) > 1 else "en"  # Language

        payload = {
            "q": query,
            "num": max_results,
            "gl": gl,  # Google country code
            "hl": hl,  # Interface language
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        results = []
        for i, item in enumerate(data.get("organic", [])[:max_results], 1):
            results.append(
                SearchResultItem(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    citation_id=i,
                    position=i,
                    page_age=item.get("date"),  # Serper sometimes includes date
                )
            )
        return results

    async def _search_ddgs(self, query: str, max_results: int) -> list[SearchResultItem]:
        """Search using ddgs (DuckDuckGo)."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _sync_search():
            from ddgs import DDGS

            ddgs = DDGS()
            raw_results = ddgs.text(
                query,
                max_results=max_results,
                region=self._region,
                safesearch=self._safesearch,
            )
            return raw_results

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            raw_results = await loop.run_in_executor(executor, _sync_search)

        results = []
        for i, item in enumerate(raw_results or [], 1):
            results.append(
                SearchResultItem(
                    title=item.get("title", ""),
                    url=item.get("href", ""),
                    snippet=item.get("body", ""),
                    citation_id=i,
                    position=i,
                )
            )
        return results

    def get_cache_stats(self) -> dict[str, Any] | None:
        """Get cache statistics for debugging."""
        if not self._cache:
            return None

        stats = self._cache.get_stats()
        return {
            "search_entries": stats.search_entries,
            "fetch_entries": stats.fetch_entries,
            "total_size_mb": stats.total_size_bytes / (1024 * 1024),
            "total_hits": stats.total_hits,
        }

    def get_budget_usage(self) -> dict[str, Any] | None:
        """Get budget usage summary."""
        if not self._budget:
            return None
        return self._budget.get_usage_summary()

    def reset_budget(self) -> None:
        """Reset budget counters (for new session)."""
        if self._budget:
            self._budget.reset()

    def reset_security(self) -> None:
        """Reset security provenance tracking (for new session)."""
        if self._security:
            self._security.reset()

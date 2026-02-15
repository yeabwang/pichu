"""SQLite-based cache for web operations.

Features:
- Persistent SQLite storage
- TTL-based expiration
- Automatic cleanup of expired entries
- Cache modes: live, cached, offline
- Thread-safe operations
- Size limits with LRU eviction
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generator


class CacheMode(str, Enum):
    """Cache operation modes."""

    LIVE = "live"  # Always fetch fresh, update cache
    CACHED = "cached"  # Return cache if valid, else fetch
    OFFLINE = "offline"  # Only return cache, fail if not found


@dataclass
class CacheEntry:
    """A cached item with metadata."""

    key: str
    value: Any
    created_at: float
    expires_at: float
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def ttl_remaining(self) -> float:
        return max(0, self.expires_at - time.time())


@dataclass
class CacheStats:
    """Cache statistics."""

    search_entries: int = 0
    fetch_entries: int = 0
    total_size_bytes: int = 0
    oldest_entry: float | None = None
    newest_entry: float | None = None
    total_hits: int = 0


class WebCache:
    """SQLite-based cache for web search and fetch operations.

    Thread-safe and supports TTL-based expiration with automatic cleanup.
    """

    # SQL schema
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS search_cache (
        cache_key TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        backend TEXT NOT NULL,
        max_results INTEGER NOT NULL,
        results TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        hit_count INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS fetch_cache (
        url_hash TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        content TEXT NOT NULL,
        title TEXT,
        mime_type TEXT,
        content_length INTEGER NOT NULL,
        fetched_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        hit_count INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_search_expires ON search_cache(expires_at);
    CREATE INDEX IF NOT EXISTS idx_fetch_expires ON fetch_cache(expires_at);
    CREATE INDEX IF NOT EXISTS idx_search_created ON search_cache(created_at);
    CREATE INDEX IF NOT EXISTS idx_fetch_created ON fetch_cache(fetched_at);
    """

    def __init__(
        self,
        cache_dir: Path | str,
        search_ttl_hours: float = 24.0,
        fetch_ttl_hours: float = 168.0,  # 7 days
        max_size_mb: float = 100.0,
    ) -> None:
        """Initialize the cache.

        Args:
            cache_dir: Directory to store cache database
            search_ttl_hours: TTL for search results in hours
            fetch_ttl_hours: TTL for fetched pages in hours
            max_size_mb: Maximum cache size in megabytes
        """
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._cache_dir / "web_cache.db"

        self._search_ttl = search_ttl_hours * 3600  # Convert to seconds
        self._fetch_ttl = fetch_ttl_hours * 3600
        self._max_size_bytes = int(max_size_mb * 1024 * 1024)

        self._lock = threading.Lock()
        self._local = threading.local()

        # Initialize schema
        with self._get_connection() as conn:
            conn.executescript(self._SCHEMA)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level="DEFERRED",
            )
            self._local.conn.row_factory = sqlite3.Row

        try:
            yield self._local.conn
        except Exception:
            self._local.conn.rollback()
            raise

    def _generate_search_key(self, query: str, backend: str, max_results: int) -> str:
        """Generate a unique cache key for search queries."""
        key_data = f"{query.lower().strip()}|{backend}|{max_results}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def _generate_url_hash(self, url: str) -> str:
        """Generate a unique hash for URLs."""
        return hashlib.sha256(url.encode()).hexdigest()[:32]

    # Search Cache Operations

    def get_search(self, query: str, backend: str, max_results: int) -> list[dict[str, str]] | None:
        """Get cached search results.

        Returns None if not found or expired.
        """
        cache_key = self._generate_search_key(query, backend, max_results)
        now = time.time()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT results, expires_at, hit_count
                    FROM search_cache
                    WHERE cache_key = ? AND expires_at > ?
                    """,
                    (cache_key, now),
                )
                row = cursor.fetchone()

                if row is None:
                    return None

                # Update hit count
                conn.execute(
                    """
                    UPDATE search_cache
                    SET hit_count = hit_count + 1
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                )
                conn.commit()

                return json.loads(row["results"])

    def set_search(
        self,
        query: str,
        backend: str,
        max_results: int,
        results: list[dict[str, str]],
        ttl_hours: float | None = None,
    ) -> None:
        """Cache search results."""
        cache_key = self._generate_search_key(query, backend, max_results)
        now = time.time()
        ttl = (ttl_hours * 3600) if ttl_hours else self._search_ttl
        expires_at = now + ttl

        results_json = json.dumps(results)

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO search_cache
                    (cache_key, query, backend, max_results, results, created_at, expires_at, hit_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        cache_key,
                        query,
                        backend,
                        max_results,
                        results_json,
                        now,
                        expires_at,
                    ),
                )
                conn.commit()

        # Trigger cleanup if needed
        self._maybe_cleanup()

    # Fetch Cache Operations

    def get_fetch(self, url: str) -> dict[str, Any] | None:
        """Get cached page content.

        Returns dict with 'content', 'title', 'mime_type' or None.
        """
        url_hash = self._generate_url_hash(url)
        now = time.time()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT content, title, mime_type, content_length, expires_at
                    FROM fetch_cache
                    WHERE url_hash = ? AND expires_at > ?
                    """,
                    (url_hash, now),
                )
                row = cursor.fetchone()

                if row is None:
                    return None

                # Update hit count
                conn.execute(
                    """
                    UPDATE fetch_cache
                    SET hit_count = hit_count + 1
                    WHERE url_hash = ?
                    """,
                    (url_hash,),
                )
                conn.commit()

                return {
                    "content": row["content"],
                    "title": row["title"],
                    "mime_type": row["mime_type"],
                    "content_length": row["content_length"],
                    "cached": True,
                }

    def set_fetch(
        self,
        url: str,
        content: str,
        title: str | None = None,
        mime_type: str | None = None,
        ttl_hours: float | None = None,
    ) -> None:
        """Cache fetched page content."""
        url_hash = self._generate_url_hash(url)
        now = time.time()
        ttl = (ttl_hours * 3600) if ttl_hours else self._fetch_ttl
        expires_at = now + ttl
        content_length = len(content.encode("utf-8", errors="replace"))

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fetch_cache
                    (url_hash, url, content, title, mime_type, content_length, fetched_at, expires_at, hit_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        url_hash,
                        url,
                        content,
                        title,
                        mime_type,
                        content_length,
                        now,
                        expires_at,
                    ),
                )
                conn.commit()

        self._maybe_cleanup()

    # Cache Management

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        with self._lock:
            with self._get_connection() as conn:
                # Search cache stats
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) as count, SUM(LENGTH(results)) as size,
                           MIN(created_at) as oldest, MAX(created_at) as newest,
                           SUM(hit_count) as hits
                    FROM search_cache
                    """
                )
                search_row = cursor.fetchone()

                # Fetch cache stats
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) as count, SUM(content_length) as size,
                           MIN(fetched_at) as oldest, MAX(fetched_at) as newest,
                           SUM(hit_count) as hits
                    FROM fetch_cache
                    """
                )
                fetch_row = cursor.fetchone()

                search_size = search_row["size"] or 0
                fetch_size = fetch_row["size"] or 0

                oldest = None
                if search_row["oldest"] and fetch_row["oldest"]:
                    oldest = min(search_row["oldest"], fetch_row["oldest"])
                elif search_row["oldest"]:
                    oldest = search_row["oldest"]
                elif fetch_row["oldest"]:
                    oldest = fetch_row["oldest"]

                newest = None
                if search_row["newest"] and fetch_row["newest"]:
                    newest = max(search_row["newest"], fetch_row["newest"])
                elif search_row["newest"]:
                    newest = search_row["newest"]
                elif fetch_row["newest"]:
                    newest = fetch_row["newest"]

                return CacheStats(
                    search_entries=search_row["count"] or 0,
                    fetch_entries=fetch_row["count"] or 0,
                    total_size_bytes=search_size + fetch_size,
                    oldest_entry=oldest,
                    newest_entry=newest,
                    total_hits=(search_row["hits"] or 0) + (fetch_row["hits"] or 0),
                )

    def clear_expired(self) -> tuple[int, int]:
        """Remove expired entries from both caches.

        Returns (search_removed, fetch_removed).
        """
        now = time.time()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM search_cache WHERE expires_at <= ?",
                    (now,),
                )
                search_removed = cursor.rowcount

                cursor = conn.execute(
                    "DELETE FROM fetch_cache WHERE expires_at <= ?",
                    (now,),
                )
                fetch_removed = cursor.rowcount

                conn.commit()

                # Vacuum to reclaim space
                conn.execute("VACUUM")

        return search_removed, fetch_removed

    def clear_all(self) -> tuple[int, int]:
        """Clear all cache entries.

        Returns (search_removed, fetch_removed).
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM search_cache")
                search_removed = cursor.rowcount

                cursor = conn.execute("DELETE FROM fetch_cache")
                fetch_removed = cursor.rowcount

                conn.commit()
                conn.execute("VACUUM")

        return search_removed, fetch_removed

    def _maybe_cleanup(self) -> None:
        """Cleanup if cache exceeds size limit."""
        stats = self.get_stats()

        if stats.total_size_bytes <= self._max_size_bytes:
            return

        # First, clear expired entries
        self.clear_expired()

        # Check again
        stats = self.get_stats()
        if stats.total_size_bytes <= self._max_size_bytes:
            return

        # LRU eviction: remove oldest entries until under limit
        target_size = int(self._max_size_bytes * 0.8)  # Leave 20% headroom

        with self._lock:
            with self._get_connection() as conn:
                # Evict oldest search entries
                while stats.total_size_bytes > target_size and stats.search_entries > 0:
                    conn.execute(
                        """
                        DELETE FROM search_cache
                        WHERE cache_key IN (
                            SELECT cache_key FROM search_cache
                            ORDER BY created_at ASC LIMIT 10
                        )
                        """
                    )
                    conn.commit()
                    stats = self.get_stats()

                # Evict oldest fetch entries
                while stats.total_size_bytes > target_size and stats.fetch_entries > 0:
                    conn.execute(
                        """
                        DELETE FROM fetch_cache
                        WHERE url_hash IN (
                            SELECT url_hash FROM fetch_cache
                            ORDER BY fetched_at ASC LIMIT 10
                        )
                        """
                    )
                    conn.commit()
                    stats = self.get_stats()

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# Rate Limiter


class RateLimiter:
    """Token bucket rate limiter for API calls.

    Thread-safe with configurable rates per backend.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        burst_size: int = 10,
    ) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum sustained request rate
            burst_size: Maximum burst of requests allowed
        """
        self._rate = requests_per_minute / 60.0  # Requests per second
        self._burst = burst_size
        self._tokens = float(burst_size)
        self._last_update = time.time()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Acquire a rate limit token.

        Blocks until a token is available or timeout is reached.

        Returns True if token acquired, False if timeout.
        """
        deadline = time.time() + timeout

        while True:
            with self._lock:
                now = time.time()

                # Refill tokens based on elapsed time
                elapsed = now - self._last_update
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last_update = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

            # Check timeout
            if time.time() >= deadline:
                return False

            # Calculate wait time for next token
            with self._lock:
                wait_time = (1.0 - self._tokens) / self._rate

            # Sleep with small intervals to check timeout
            time.sleep(min(wait_time, 0.1))

    def get_wait_time(self) -> float:
        """Get estimated wait time for next available token."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            current_tokens = min(self._burst, self._tokens + elapsed * self._rate)

            if current_tokens >= 1.0:
                return 0.0

            return (1.0 - current_tokens) / self._rate


class MultiRateLimiter:
    """Rate limiter with separate limits per backend."""

    def __init__(
        self,
        serper_rpm: int = 50,
        ddgs_rpm: int = 30,
        fetch_rpm: int = 60,
    ) -> None:
        self._limiters = {
            "serper": RateLimiter(serper_rpm, burst_size=5),
            "ddgs": RateLimiter(ddgs_rpm, burst_size=3),
            "fetch": RateLimiter(fetch_rpm, burst_size=10),
        }

    def acquire(self, backend: str, timeout: float = 30.0) -> bool:
        """Acquire token for specific backend."""
        limiter = self._limiters.get(backend)
        if limiter is None:
            return True  # Unknown backend, no limit
        return limiter.acquire(timeout)

    def get_wait_time(self, backend: str) -> float:
        """Get wait time for specific backend."""
        limiter = self._limiters.get(backend)
        if limiter is None:
            return 0.0
        return limiter.get_wait_time()


# Retry Logic


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, last_error: Exception | None = None):
        super().__init__(message)
        self.last_error = last_error


async def retry_async(
    func,
    config: RetryConfig | None = None,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """Retry an async function with exponential backoff.

    Args:
        func: Async callable to retry
        config: Retry configuration
        retryable_exceptions: Exception types that should trigger retry

    Returns:
        Result from successful function call

    Raises:
        RetryError: If all attempts fail
    """
    import asyncio
    import secrets

    cfg = config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(cfg.max_attempts):
        try:
            return await func()
        except retryable_exceptions as e:
            last_error = e

            if attempt == cfg.max_attempts - 1:
                break

            # Calculate delay with exponential backoff
            delay = min(
                cfg.base_delay * (cfg.exponential_base**attempt),
                cfg.max_delay,
            )

            # Add jitter to prevent thundering herd
            if cfg.jitter:
                delay = delay * (0.5 + (secrets.randbelow(10_000) / 10_000))

            await asyncio.sleep(delay)

    raise RetryError(
        f"Failed after {cfg.max_attempts} attempts",
        last_error=last_error,
    )

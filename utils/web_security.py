"""Web security controls - Domain filtering, URL provenance, anti-exfiltration.

Provides security features including:
- Domain allow/block lists
- URL provenance tracking (only fetch URLs from search results)
- Private network blocking
- Scheme validation
"""

from __future__ import annotations

import fnmatch
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from utils.web_types import WebToolError, WebToolErrorCode

if TYPE_CHECKING:
    from utils.web_types import SearchResultItem

logger = logging.getLogger(__name__)

# Security Configuration


@dataclass
class WebSecurityConfig:
    """Security configuration for web tools."""

    # Domain filtering
    allowed_domains: list[str] = field(default_factory=list)  # Empty = all allowed
    blocked_domains: list[str] = field(
        default_factory=lambda: [
            # Common malicious patterns
            "*.malware.*",
            "*.phishing.*",
            "*.scam.*",
        ]
    )

    # URL validation
    allow_ip_addresses: bool = False  # Block http://192.168.1.1
    allow_localhost: bool = False  # Block http://localhost, http://127.0.0.1
    allow_private_networks: bool = False  # Block 10.x, 172.16.x, 192.168.x

    # Scheme restrictions
    allowed_schemes: list[str] = field(default_factory=lambda: ["https", "http"])

    # Redirect limits
    max_redirects: int = 5

    # Anti-exfiltration behavior
    require_url_provenance: bool = True  # Only fetch URLs from search results or user

    # Content restrictions
    max_content_bytes: int = 10_000_000  # 10MB per fetch
    blocked_content_types: list[str] = field(
        default_factory=lambda: [
            "application/x-executable",
            "application/x-dosexec",
            "application/x-msdownload",
        ]
    )


# URL Validator


class URLValidator:
    """Validates URLs against security rules."""

    # Private network ranges
    PRIVATE_RANGES = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),  # Link-local
        ipaddress.ip_network("::1/128"),  # IPv6 localhost
        ipaddress.ip_network("fc00::/7"),  # IPv6 private
        ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ]

    LOCALHOST_PATTERNS = [
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",  # noqa: S104
        "localhost.localdomain",
    ]

    def __init__(self, config: WebSecurityConfig):
        self.config = config

    def validate(self, url: str) -> tuple[bool, WebToolError | None]:
        """Validate a URL against all security rules.

        Returns (is_valid, error_or_none).
        """
        try:
            parsed = urlparse(url)
        except Exception as e:
            return False, WebToolError(
                code=WebToolErrorCode.HTTP_ERROR,
                message=f"Invalid URL format: {e}",
                details={"url": url},
            )

        # Check scheme
        if parsed.scheme not in self.config.allowed_schemes:
            return False, WebToolError(
                code=WebToolErrorCode.INVALID_SCHEME,
                message=f"Scheme '{parsed.scheme}' not allowed",
                details={"url": url, "allowed": self.config.allowed_schemes},
            )

        hostname = parsed.hostname or ""

        # Check localhost
        if not self.config.allow_localhost:
            if hostname.lower() in self.LOCALHOST_PATTERNS:
                return False, WebToolError(
                    code=WebToolErrorCode.LOCALHOST_BLOCKED,
                    message="Localhost URLs are not allowed",
                    details={"url": url, "hostname": hostname},
                )

        # Check if it's an IP address
        is_ip = self._is_ip_address(hostname)

        if is_ip and not self.config.allow_ip_addresses:
            return False, WebToolError(
                code=WebToolErrorCode.IP_ADDRESS_BLOCKED,
                message="Direct IP address URLs are not allowed",
                details={"url": url, "ip": hostname},
            )

        # Check private networks
        if is_ip and not self.config.allow_private_networks:
            if self._is_private_ip(hostname):
                return False, WebToolError(
                    code=WebToolErrorCode.PRIVATE_NETWORK_BLOCKED,
                    message="Private network URLs are not allowed",
                    details={"url": url, "ip": hostname},
                )

        # Check domain allow list
        if self.config.allowed_domains:
            if not self._matches_pattern_list(hostname, self.config.allowed_domains):
                return False, WebToolError(
                    code=WebToolErrorCode.DOMAIN_NOT_ALLOWED,
                    message=f"Domain '{hostname}' is not in allow list",
                    details={"url": url, "domain": hostname},
                )

        # Check domain block list
        if self._matches_pattern_list(hostname, self.config.blocked_domains):
            return False, WebToolError(
                code=WebToolErrorCode.DOMAIN_BLOCKED,
                message=f"Domain '{hostname}' is blocked",
                details={"url": url, "domain": hostname},
            )

        return True, None

    def _is_ip_address(self, hostname: str) -> bool:
        """Check if hostname is an IP address."""
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    def _is_private_ip(self, hostname: str) -> bool:
        """Check if IP address is in a private range."""
        try:
            ip = ipaddress.ip_address(hostname)
            for network in self.PRIVATE_RANGES:
                if ip in network:
                    return True
            return False
        except ValueError:
            return False

    def _matches_pattern_list(self, hostname: str, patterns: list[str]) -> bool:
        """Check if hostname matches any pattern in list."""
        hostname_lower = hostname.lower()
        for pattern in patterns:
            pattern_lower = pattern.lower()
            # Support wildcards like *.example.com
            if fnmatch.fnmatch(hostname_lower, pattern_lower):
                return True
            # Also check if pattern matches as a suffix
            if pattern_lower.startswith("*."):
                suffix = pattern_lower[1:]  # Remove *
                if hostname_lower.endswith(suffix):
                    return True
        return False


# URL Provenance Tracker


class URLProvenanceTracker:
    """Track URLs that the model has legitimately seen.

    This prevents the model from hallucinating URLs or exfiltrating data
    to arbitrary endpoints. Only URLs from:
    - Search results
    - User-provided input
    - Same-domain navigation

    can be fetched.
    """

    def __init__(self, allow_same_domain: bool = True):
        self._seen_urls: set[str] = set()
        self._seen_domains: set[str] = set()
        self._url_sources: dict[str, str] = {}  # url -> source type
        self._allow_same_domain = allow_same_domain

    def register_from_search(self, results: list["SearchResultItem"]) -> None:
        """Register URLs from search results as valid."""
        for r in results:
            self._register_url(r.url, "search")

    def register_from_user(self, url: str) -> None:
        """Register URL explicitly provided by user."""
        self._register_url(url, "user")

    def register_url(self, url: str, source: str = "tool") -> None:
        """Register a URL with a custom source."""
        self._register_url(url, source)

    def _register_url(self, url: str, source: str) -> None:
        """Internal URL registration."""
        self._seen_urls.add(url)
        self._url_sources[url] = source

        # Also register the domain
        try:
            parsed = urlparse(url)
            if parsed.netloc:
                self._seen_domains.add(parsed.netloc.lower())
        except Exception as e:
            logger.debug("Failed to parse domain from URL '%s': %s", url, e)

    def is_allowed(self, url: str) -> tuple[bool, WebToolError | None]:
        """Check if URL can be fetched based on provenance.

        Returns (is_allowed, error_or_none).
        """
        # Exact URL match
        if url in self._seen_urls:
            return True, None

        # Same-domain check
        if self._allow_same_domain:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                if domain in self._seen_domains:
                    return True, None
            except Exception as e:
                return False, WebToolError(
                    code=WebToolErrorCode.HTTP_ERROR,
                    message=f"Invalid URL format: {e}",
                    details={"url": url},
                )

        return False, WebToolError(
            code=WebToolErrorCode.URL_PROVENANCE_FAILED,
            message="URL not from search results or user input",
            details={
                "url": url,
                "hint": "Fetch URLs from search results or ask user to provide URL",
            },
        )

    def get_source(self, url: str) -> str | None:
        """Get the source of a registered URL."""
        return self._url_sources.get(url)

    def get_seen_domains(self) -> set[str]:
        """Get all domains that have been seen."""
        return self._seen_domains.copy()

    def get_seen_urls(self) -> set[str]:
        """Get all URLs that have been seen."""
        return self._seen_urls.copy()

    def clear(self) -> None:
        """Clear all tracked URLs."""
        self._seen_urls.clear()
        self._seen_domains.clear()
        self._url_sources.clear()


# Combined Security Manager


class WebSecurityManager:
    """Combines all security checks into a single interface."""

    def __init__(self, config: WebSecurityConfig | None = None):
        self.config = config or WebSecurityConfig()
        self.validator = URLValidator(self.config)
        self.provenance = URLProvenanceTracker(allow_same_domain=True)

    def check_url(self, url: str) -> tuple[bool, WebToolError | None]:
        """Run all security checks on a URL.

        Returns (is_allowed, error_or_none).
        """
        # First, validate URL format and restrictions
        valid, error = self.validator.validate(url)
        if not valid:
            return False, error

        # Then check provenance if required
        if self.config.require_url_provenance:
            allowed, error = self.provenance.is_allowed(url)
            if not allowed:
                return False, error

        return True, None

    def check_search_allowed(self, query: str) -> tuple[bool, WebToolError | None]:
        """Check if a search query is allowed.

        Validates query is not empty and within length limits.
        """
        if not query or not query.strip():
            return False, WebToolError(
                code=WebToolErrorCode.INVALID_QUERY,
                message="Search query cannot be empty",
            )

        # Query length validation (matches typical API limits)
        MAX_QUERY_LENGTH = 2000
        if len(query) > MAX_QUERY_LENGTH:
            return False, WebToolError(
                code=WebToolErrorCode.INVALID_QUERY,
                message=f"Search query too long ({len(query)} chars, max {MAX_QUERY_LENGTH})",
                details={"length": len(query), "max_length": MAX_QUERY_LENGTH},
            )

        return True, None

    def register_search_results(self, results: list["SearchResultItem"]) -> None:
        """Register URLs from search results."""
        self.provenance.register_from_search(results)

    def register_user_url(self, url: str) -> None:
        """Register a user-provided URL."""
        self.provenance.register_from_user(url)

    def bypass_provenance_for_url(self, url: str) -> None:
        """Explicitly allow a URL (for user-provided URLs)."""
        self.provenance.register_url(url, "bypass")

    def reset(self) -> None:
        """Reset provenance tracking (for new session)."""
        self.provenance.clear()


# DNS Resolution Check


def resolve_and_check_ip(hostname: str, config: WebSecurityConfig) -> tuple[bool, WebToolError | None]:
    """Resolve hostname and check if resulting IP is allowed.

    This catches DNS rebinding attacks where a domain resolves
    to a private IP.
    """
    if config.allow_private_networks:
        return True, None

    try:
        # Resolve hostname to IP
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)

        # Check if resolved IP is private
        for network in URLValidator.PRIVATE_RANGES:
            if ip in network:
                return False, WebToolError(
                    code=WebToolErrorCode.PRIVATE_NETWORK_BLOCKED,
                    message=f"Domain '{hostname}' resolves to private IP",
                    details={"hostname": hostname, "resolved_ip": ip_str},
                )

        return True, None

    except socket.gaierror:
        # DNS resolution failed - let the actual request handle this
        return True, None
    except Exception:
        return True, None

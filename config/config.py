"""Centralized configuration for Pichu."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator
from pydantic import Field as PydanticField

# Singleton instance
_config_instance: Config | None = None

HOOK_EVENT_NAMES: tuple[str, ...] = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStop",
    "Notification",
    "PreCompact",
)


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with a fallback."""
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.environ.get(key)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes")


def _env_float(key: str, default: float) -> float:
    """Parse a float environment variable."""
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _normalize_mcp_servers(raw_servers: dict[str, Any]) -> dict[str, MCPServerConfig]:
    """Coerce TOML `mcp_servers` payloads into validated model objects."""
    normalized: dict[str, MCPServerConfig] = {}
    for server_name, server_data in raw_servers.items():
        if isinstance(server_data, MCPServerConfig):
            normalized[server_name] = server_data
            continue
        if isinstance(server_data, dict):
            normalized[server_name] = MCPServerConfig(**server_data)
            continue
        raise TypeError(f"mcp_servers.{server_name} must be a mapping, got {type(server_data).__name__}")
    return normalized


def _normalize_hook_handlers(raw_hooks: Any) -> list[HookHandlerConfig]:
    """Normalize hook handler payloads from TOML dictionaries."""
    if isinstance(raw_hooks, HookHandlerConfig):
        return [raw_hooks]
    if not isinstance(raw_hooks, list):
        raw_hooks = [raw_hooks]

    handlers: list[HookHandlerConfig] = []
    for raw_handler in raw_hooks:
        if isinstance(raw_handler, HookHandlerConfig):
            handlers.append(raw_handler)
            continue
        if isinstance(raw_handler, dict):
            handlers.append(
                HookHandlerConfig(
                    type=raw_handler.get("type", "command"),
                    command=raw_handler.get("command", ""),
                    timeout=int(raw_handler.get("timeout", 60)),
                    async_=bool(raw_handler.get("async", False)),
                    status_message=raw_handler.get("status_message"),
                )
            )
            continue
        raise TypeError(f"Hook handler must be a mapping, got {type(raw_handler).__name__}")
    return handlers


def _normalize_hook_matchers(raw_matchers: Any) -> list[HookMatcherConfig]:
    """Normalize hook matcher payloads from TOML dictionaries."""
    if isinstance(raw_matchers, HookMatcherConfig):
        return [raw_matchers]
    if not isinstance(raw_matchers, list):
        raw_matchers = [raw_matchers]

    parsed_matchers: list[HookMatcherConfig] = []
    for raw_matcher in raw_matchers:
        if isinstance(raw_matcher, HookMatcherConfig):
            parsed_matchers.append(raw_matcher)
            continue
        if isinstance(raw_matcher, dict):
            parsed_matchers.append(
                HookMatcherConfig(
                    matcher=raw_matcher.get("matcher"),
                    hooks=_normalize_hook_handlers(raw_matcher.get("hooks", [])),
                )
            )
            continue
        raise TypeError(f"Hook matcher must be a mapping, got {type(raw_matcher).__name__}")
    return parsed_matchers


def _merge_hooks_config(current: Any, raw_hooks: dict[str, Any]) -> HooksConfig:
    """Merge a raw `hooks` mapping into a typed `HooksConfig` object."""
    hooks_config = current if isinstance(current, HooksConfig) else HooksConfig()
    if "disabled" in raw_hooks:
        hooks_config.disabled = bool(raw_hooks["disabled"])

    for event_name in HOOK_EVENT_NAMES:
        if event_name in raw_hooks:
            setattr(
                hooks_config,
                event_name,
                _normalize_hook_matchers(raw_hooks[event_name]),
            )
    return hooks_config


def _deep_update(obj: Any, data: dict[str, Any]) -> None:
    """Apply nested dictionary updates onto config dataclasses and enums."""
    if not isinstance(data, dict):
        raise TypeError(f"Config update payload must be a mapping, got {type(data).__name__}")

    for key, value in data.items():
        if not hasattr(obj, key):
            continue

        current = getattr(obj, key)

        if key == "mcp_servers":
            if not isinstance(value, dict):
                raise TypeError("mcp_servers must be a mapping of server definitions")
            setattr(obj, key, _normalize_mcp_servers(value))
            continue

        if key == "hooks":
            if not isinstance(value, dict):
                raise TypeError("hooks must be a mapping")
            setattr(obj, key, _merge_hooks_config(current, value))
            continue

        if is_dataclass(current) and isinstance(value, dict):
            _deep_update(current, value)
            continue

        if isinstance(current, Enum) and isinstance(value, str):
            setattr(obj, key, type(current)(value))
            continue

        setattr(obj, key, value)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 5
    min_wait: float = 2.0
    max_wait: float = 60.0
    multiplier: float = 1.0


@dataclass
class LLMConfig:
    """Configuration for LLM client - provider agnostic."""

    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))

    # These come from config files (system → project)
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "mistralai/devstral-2512:free"
    temperature: float = 0.3
    timeout: float = 120.0
    max_tokens: int | None = None
    retry: RetryConfig = field(default_factory=RetryConfig)


@dataclass
class LimitsConfig:
    """Token and size limits."""

    max_turns: int = 100
    max_tool_output_tokens: int = 50_000
    max_file_size: int = 10 * 1024 * 1024  # 10 MB
    context_window: int = 128_000
    # Display limits (for UI truncation)
    tool_output_display_tokens: int = 2_000
    tool_output_preview_tokens: int = 200
    # Pruning settings
    prune_protect_tokens: int = 10_000  # Protect most recent N tokens of tool output
    prune_minimum_tokens: int = 5_000  # Only prune if we can save at least N tokens
    # Compression settings
    compression_threshold: float = 0.8  # Trigger compression at this % of context_window


@dataclass
class ShellSecurityPolicy:
    """Security policy for shell command execution."""

    # Blocked command patterns (exact or glob)
    blocked_patterns: list[str] = field(
        default_factory=lambda: [
            "rm -rf /",
            "rm -rf ~",
            "rm -rf /*",
            "rm -rf $HOME",
            "dd if=/dev/zero*",
            "dd if=/dev/random*",
            "mkfs*",
            "fdisk*",
            "parted*",
            ":(){ :|:& };:",  # Fork bomb
            "chmod 777 /",
            "chmod -R 777 /",
            "shutdown*",
            "reboot*",
            "halt*",
            "poweroff*",
            "init 0",
            "init 6",
            "> /dev/sda",
            "wget * | bash",
            "curl * | bash",
            "wget * | sh",
            "curl * | sh",
        ]
    )

    # Commands requiring confirmation (glob patterns)
    confirm_patterns: list[str] = field(
        default_factory=lambda: [
            "rm -rf *",
            "rm -r *",
            "git push --force*",
            "git push -f*",
            "git reset --hard*",
            "DROP TABLE*",
            "DELETE FROM*",
            "npm publish*",
        ]
    )

    # Timeout settings
    default_timeout: int = 120
    max_timeout: int = 600


@dataclass
class ShellEnvironmentPolicy:
    """Policy for shell environment variable exposure."""

    # If True, don't apply default exclusions
    ignore_default_excludes: bool = False

    # Glob patterns for vars to exclude from shell env
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "AWS_*",
            "GCP_*",
            "AZURE_*",
            "SECRET*",
            "*PASSWORD*",
            "*API_KEY*",
            "*TOKEN*",
            "LLM_*",
            "OPENAI_*",
            "ANTHROPIC_*",
            "HUGGINGFACE_*",
            "GITHUB_TOKEN",
            "NPM_TOKEN",
            "PYPI_TOKEN",
        ]
    )

    # Explicit vars to set in shell environment
    set_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class ShellConfig:
    """Complete shell configuration."""

    security: ShellSecurityPolicy = field(default_factory=ShellSecurityPolicy)
    environment: ShellEnvironmentPolicy = field(default_factory=ShellEnvironmentPolicy)

    # Output limits
    max_output_bytes: int = 100 * 1024  # 100KB
    max_output_lines: int = 1000


@dataclass
class SafetySandboxConfig:
    """Configuration for filesystem safety sandbox."""

    enabled: bool = True
    restrict_to_cwd: bool = True
    allow_temp_writes: bool = False
    allow_executables: bool = False
    restrict_reads_to_allowed_dirs: bool = False
    allowed_directories: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(
        default_factory=lambda: [
            ".git/hooks",
            ".ssh",
            ".gnupg",
            ".aws/credentials",
            ".env",
            "id_rsa",
            "id_ed25519",
            ".bashrc",
            ".zshrc",
            ".profile",
            "authorized_keys",
        ]
    )
    blocked_extensions: list[str] = field(
        default_factory=lambda: [
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".bat",
            ".cmd",
            ".ps1",
            ".sh",
        ]
    )
    sensitive_read_patterns: list[str] = field(
        default_factory=lambda: [
            ".env",
            ".env.",
            ".ssh/id_",
            ".gnupg",
            ".aws/credentials",
            "id_rsa",
            "id_ed25519",
            "authorized_keys",
        ]
    )


@dataclass
class SafetyConfig:
    """Top-level safety configuration."""

    sandbox: SafetySandboxConfig = field(default_factory=SafetySandboxConfig)


@dataclass
class LoggingRedactionConfig:
    """Configuration for sensitive value redaction in logs."""

    enabled: bool = True
    replacement: str = "[REDACTED]"
    patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([^\s,'\"]+)",
            r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]+",
            r"(?i)ghp_[A-Za-z0-9]{20,}",
            r"(?i)sk-[A-Za-z0-9]{16,}",
        ]
    )
    sensitive_key_fragments: list[str] = field(
        default_factory=lambda: [
            "api_key",
            "apikey",
            "token",
            "password",
            "secret",
            "authorization",
            "credential",
        ]
    )


@dataclass
class LoggingConsoleConfig:
    """Console logging configuration."""

    enabled: bool = False
    level: str = "DEBUG"
    use_rich: bool = True
    show_path: bool = False
    markup: bool = True
    format: str = "%(message)s"


@dataclass
class LoggingFileConfig:
    """Rotating runtime log file configuration."""

    enabled: bool = True
    level: str = "INFO"
    path: str = "logs/app/pichu.log"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5
    json: bool = False
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


@dataclass
class LoggingAuditConfig:
    """Dedicated security/audit logging stream configuration."""

    enabled: bool = True
    level: str = "INFO"
    path: str = "logs/security/audit.log"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5
    json: bool = True
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


@dataclass
class LoggingConfig:
    """Top-level logging configuration."""

    level: str = "INFO"
    per_logger_levels: dict[str, str] = field(default_factory=dict)
    console: LoggingConsoleConfig = field(default_factory=LoggingConsoleConfig)
    file: LoggingFileConfig = field(default_factory=LoggingFileConfig)
    audit: LoggingAuditConfig = field(default_factory=LoggingAuditConfig)
    redaction: LoggingRedactionConfig = field(default_factory=LoggingRedactionConfig)


@dataclass
class ListDirConfig:
    """Configuration for list_dir tool."""

    # Default traversal depth
    default_depth: int = 2
    max_depth: int = 5

    # Pagination defaults
    default_limit: int = 100
    max_limit: int = 500

    # Directories to always skip (never recurse into)
    skip_dirs: list[str] = field(
        default_factory=lambda: [
            # Package managers
            "node_modules",
            ".npm",
            ".yarn",
            ".pnpm-store",
            # Python
            "__pycache__",
            ".venv",
            "venv",
            ".env",
            "env",
            ".eggs",
            ".tox",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "*.egg-info",
            # Build outputs
            "dist",
            "build",
            "out",
            "target",
            ".next",
            ".nuxt",
            ".output",
            # Version control
            ".git",
            ".svn",
            ".hg",
            # IDE/Editor
            ".idea",
        ]
    )

    # Whether to respect .gitignore by default
    respect_gitignore: bool = True

    # Whether to include hidden files by default
    include_hidden: bool = False


@dataclass
class GrepConfig:
    """Configuration for grep tool."""

    # Result limits
    default_limit: int = 100
    max_limit: int = 2000

    # Context defaults
    default_context_lines: int = 0
    max_context_lines: int = 10

    # File handling
    max_file_size: int = 1_000_000  # 1MB
    timeout: int = 30  # seconds

    # Default excludes (in addition to .gitignore)
    default_excludes: list[str] = field(
        default_factory=lambda: [
            "*.min.js",
            "*.min.css",
            "*.map",
            "*.lock",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ]
    )

    # Binary extensions to skip
    binary_extensions: list[str] = field(
        default_factory=lambda: [
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".bin",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".ico",
            ".webp",
            ".svg",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".wav",
            ".flac",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
            ".rar",
            ".bz2",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".pyc",
            ".pyo",
            ".class",
            ".o",
            ".obj",
            ".a",
            ".lib",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".otf",
            ".sqlite",
            ".db",
            ".pickle",
            ".pkl",
        ]
    )


@dataclass
class GlobConfig:
    """Configuration for glob/file search tool."""

    # Result limits
    default_limit: int = 200
    max_limit: int = 5000

    # Timeout
    timeout: int = 30  # seconds

    # Default directories to skip
    skip_dirs: list[str] = field(
        default_factory=lambda: [
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            ".git",
            "dist",
            "build",
            ".next",
            "target",
            ".idea",
        ]
    )


@dataclass
class WebSecurityConfig:
    """Security configuration for web tools."""

    # Domain filtering
    allowed_domains: list[str] = field(default_factory=list)  # Empty = all allowed
    blocked_domains: list[str] = field(
        default_factory=lambda: [
            "*.malware.*",
            "*.phishing.*",
            "*.scam.*",
        ]
    )

    # URL validation
    allow_ip_addresses: bool = False  # Block http://192.168.1.1
    allow_localhost: bool = False  # Block http://localhost
    allow_private_networks: bool = False  # Block 10.x, 172.16.x, etc.

    # Scheme restrictions
    allowed_schemes: list[str] = field(default_factory=lambda: ["https", "http"])

    # Anti-exfiltration behavior
    require_url_provenance: bool = True  # Only fetch URLs from search results

    # Content restrictions
    max_content_bytes: int = 10_000_000  # 10MB per fetch


@dataclass
class WebBudgetConfig:
    """Per-session budget for web tool usage."""

    # Search limits
    max_searches: int = 20
    max_results_per_search: int = 10

    # Fetch limits
    max_fetches: int = 30
    max_bytes_total: int = 20_000_000  # 20MB total per session


@dataclass
class WebCacheConfig:
    """Configuration for web cache."""

    enabled: bool = True
    mode: str = "cached"  # live, cached, offline
    cache_dir: str = ".PICHU/cache"
    search_ttl_hours: float = 24.0  # Search results expire after 24h
    fetch_ttl_hours: float = 168.0  # Fetched pages expire after 7 days
    max_size_mb: float = 100.0  # Auto-prune old entries


@dataclass
class WebRateLimitConfig:
    """Configuration for web rate limiting."""

    enabled: bool = True
    serper_rpm: int = 50  # Requests per minute
    ddgs_rpm: int = 30
    fetch_rpm: int = 60


@dataclass
class WebRetryConfig:
    """Configuration for web retry behavior."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True


@dataclass
class WebSearchConfig:
    """Configuration for web search tool."""

    # API Keys (loaded from environment)
    serper_api_key: str = field(default_factory=lambda: _env("SERPER_API_KEY"))

    # Primary backend: serper (needs API key), fallback: ddgs (free)
    default_backend: str = "serper"  # serper or ddgs
    fallback_backend: str = "ddgs"

    # Search settings
    max_results: int = 5
    max_result_chars: int = 2000  # Max chars per result snippet
    region: str = "us-en"
    safesearch: str = "moderate"  # on, moderate, off

    # Cache configuration
    cache: WebCacheConfig = field(default_factory=WebCacheConfig)

    # Rate limiting
    rate_limit: WebRateLimitConfig = field(default_factory=WebRateLimitConfig)

    # Retry configuration
    retry: WebRetryConfig = field(default_factory=WebRetryConfig)

    # Security configuration
    security: WebSecurityConfig = field(default_factory=WebSecurityConfig)

    # Budget configuration
    budget: WebBudgetConfig = field(default_factory=WebBudgetConfig)


@dataclass
class WebFetchConfig:
    """Configuration for web fetch tool."""

    # Fetch/scrape settings
    use_playwright: bool = True  # Use playwright if available
    playwright_timeout: int = 10000  # ms
    httpx_timeout: int = 30  # seconds
    verify_ssl: bool = True

    # Content processing
    max_content_length: int = 50000  # chars
    strip_scripts: bool = True
    strip_styles: bool = True

    # User agent
    user_agent: str = "Pichu/1.0"

    # Find action settings
    find_context_lines: int = 2  # Lines before/after match
    find_max_matches: int = 10  # Max matches to show

    # PDF support
    pdf_enabled: bool = True  # Extract text from PDFs
    pdf_max_pages: int = 50  # Max pages to extract


@dataclass
class MemoryConfig:
    """Configuration for memory system."""

    # Enable/disable memory system
    enabled: bool = True

    # Global memory storage path (user-level, cross-project)
    # Default: ~/.pichu/memory/
    global_memory_path: Path = field(default_factory=lambda: Path.home() / ".pichu" / "memory")

    # Project memory storage path (relative to project root)
    # Default: .pichu/memory/
    project_memory_dir: str = ".pichu/memory"

    # AGENTS.md paths
    global_agents_path: Path = field(default_factory=lambda: Path.home() / ".pichu" / "AGENTS.md")
    project_agents_file: str = "AGENTS.md"
    local_agents_file: str = "AGENTS.local.md"

    # Memory behavior
    auto_load: bool = True  # Load memories on session start
    auto_save: bool = True  # Save memories after each operation
    auto_decay: bool = True  # Apply decay on load
    auto_prune: bool = True  # Prune expired memories on load

    # Context injection
    inject_memory_context: bool = True  # Include memories in system prompt
    inject_agents_context: bool = True  # Include AGENTS.md in system prompt
    max_context_memories: int = 30  # Max memories to inject
    min_context_confidence: float = 0.4  # Min confidence for injection

    # Deduplication
    similarity_threshold: float = 0.8  # Jaccard similarity for dedup


class MCPServerConfig(BaseModel):
    enabled: bool = True
    startup_timeout_sec: float = 60.0

    # stdio transport
    command: str | None = None  # e.g., "path/to/mcp_server --arg value"
    args: list[str] = PydanticField(default_factory=list)
    env: dict[str, str] = PydanticField(default_factory=dict)
    cwd: Path | None = None

    # http/sse transport
    url: str | None = None  # e.g., "http://localhost:8000/sse"
    transport: Literal["sse", "http", "auto"] | None = None
    headers: dict[str, str] = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport(self) -> MCPServerConfig:
        has_command = self.command is not None
        has_url = self.url is not None

        if not has_command and not has_url:
            raise ValueError("Either 'command' or 'url' must be set for MCPServerConfig.")
        if has_command and has_url:
            raise ValueError("Only one of 'command' or 'url' can be set for MCPServerConfig.")

        if has_command and self.transport is not None:
            raise ValueError("'transport' can only be set when 'url' is configured for MCPServerConfig.")
        if has_command and self.headers:
            raise ValueError("'headers' can only be set when 'url' is configured for MCPServerConfig.")
        if has_url and self.transport is None:
            # Preserve legacy behavior for existing URL-based configs.
            self.transport = "sse"

        return self


class ApprovalPolicy(str, Enum):
    """Approval policy for tool invocations."""

    ON_REQUEST = "on_request"  # Prompt on first use of each mutating tool
    ON_FAILURE = "on_failure"  # Auto-approve, prompt only on failure
    AUTO = "auto"  # Auto-approve all (except dangerous commands)
    AUTO_EDIT = "auto_edit"  # Auto-approve file edits, prompt for shell/network
    PLAN = "plan"  # Read-only mode - deny all mutations
    NEVER = "never"  # Deny all unless in allow rules
    YOLO = "yolo"  # Skip all permission checks


@dataclass
class PermissionRulesConfig:
    """Permission rules configuration.

    Rules follow the format 'tool_name' or 'tool_name(specifier)'.
    Evaluated in order: deny → ask → allow.  First match wins.

    Examples:
        allow = ["shell(npm run *)", "shell(git status)"]
        deny  = ["shell(curl * | bash)", "read_file(.env)"]
        ask   = ["shell(git push *)"]
    """

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)


@dataclass
class ApprovalConfig:
    """Complete approval system configuration."""

    # The base approval policy (permission mode)
    policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST

    # Fine-grained permission rules (deny/ask/allow)
    rules: PermissionRulesConfig = field(default_factory=PermissionRulesConfig)

    # Remember approvals within the current session
    remember_session: bool = True

    # Remember shell approvals permanently per project
    remember_permanent: bool = False


@dataclass
class HookHandlerConfig:
    """Configuration for a single hook handler."""

    type: str = "command"  # "command" (Phase 1), "prompt" (future)
    command: str = ""  # shell command to execute
    timeout: int = 60  # seconds before canceling
    async_: bool = False  # run in background without blocking
    status_message: str | None = None  # custom spinner message


@dataclass
class HookMatcherConfig:
    """A matcher group: pattern + list of handlers."""

    matcher: str | None = None  # regex pattern, None = match all
    hooks: list[HookHandlerConfig] = field(default_factory=list)


@dataclass
class HooksConfig:
    """Complete hooks system configuration.

    Maps hook event names to lists of matcher groups.
    Hooks are snapshotted at session start for security.

    TOML example:
        [hooks]
        disabled = false

        [[hooks.PreToolUse]]
        matcher = "shell"
        [[hooks.PreToolUse.hooks]]
        type = "command"
        command = ".PICHU/hooks/block-dangerous.py"
        timeout = 10
    """

    disabled: bool = False

    # Event fields are populated from TOML under [hooks.<EventName>]
    SessionStart: list[HookMatcherConfig] = field(default_factory=list)
    SessionEnd: list[HookMatcherConfig] = field(default_factory=list)
    UserPromptSubmit: list[HookMatcherConfig] = field(default_factory=list)
    PreToolUse: list[HookMatcherConfig] = field(default_factory=list)
    PostToolUse: list[HookMatcherConfig] = field(default_factory=list)
    PostToolUseFailure: list[HookMatcherConfig] = field(default_factory=list)
    Stop: list[HookMatcherConfig] = field(default_factory=list)
    SubagentStop: list[HookMatcherConfig] = field(default_factory=list)
    Notification: list[HookMatcherConfig] = field(default_factory=list)
    PreCompact: list[HookMatcherConfig] = field(default_factory=list)

    def to_engine_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by HookEngine."""
        from hooks.types import HookHandler, HookMatcher

        result: dict[str, list[HookMatcher]] = {}
        for event_name in HOOK_EVENT_NAMES:
            matcher_configs = getattr(self, event_name, [])
            if not matcher_configs:
                continue
            matchers = []
            for mc in matcher_configs:
                handlers = [
                    HookHandler(
                        type=hc.type,
                        command=hc.command,
                        timeout=hc.timeout,
                        async_=hc.async_,
                        status_message=hc.status_message,
                    )
                    for hc in mc.hooks
                ]
                matchers.append(HookMatcher(matcher=mc.matcher, hooks=handlers))
            if matchers:
                result[event_name] = matchers
        return result


@dataclass
class SessionPersistenceConfig:
    """Configuration for session persistence and checkpoints."""

    # Enable/disable session persistence
    enabled: bool = True

    # Storage directory (default: ~/.pichu/sessions)
    storage_dir: Path = field(default_factory=lambda: Path.home() / ".pichu" / "sessions")

    # Auto-cleanup: remove sessions older than this many days
    cleanup_days: int = 30

    # Maximum sessions to keep (prune oldest beyond this)
    max_sessions: int = 100

    # Auto-generate title from first user message
    auto_title: bool = True

    # Checkpointing (file snapshots before edits)
    checkpoints_enabled: bool = True


@dataclass
class LoopDetectionConfig:
    """Configuration for loop detection thresholds."""

    enabled: bool = True
    max_repeated_tool_calls: int = 3
    max_repeated_errors: int = 3
    max_similar_responses: int = 3
    similarity_threshold: float = 0.85
    window_size: int = 10
    action: str = "warn_and_nudge"  # "warn_and_nudge" | "pause" | "stop"


@dataclass
class Config:
    """Main application configuration."""

    # LLM settings
    llm: LLMConfig = field(default_factory=LLMConfig)

    # Limits
    limits: LimitsConfig = field(default_factory=LimitsConfig)

    # Working directory
    cwd: Path = field(default_factory=Path.cwd)

    # Instructions (from AGENT.md or config)
    developer_instructions: str | None = None
    user_instructions: str | None = None

    # Debug mode
    debug: bool = field(default_factory=lambda: _env_bool("PICHU_DEBUG"))

    # Shell configuration
    shell: ShellConfig = field(default_factory=ShellConfig)

    # Safety configuration
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    # Runtime logging configuration
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # List directory configuration
    list_dir: ListDirConfig = field(default_factory=ListDirConfig)

    # Grep configuration
    grep: GrepConfig = field(default_factory=GrepConfig)

    # Glob configuration
    glob: GlobConfig = field(default_factory=GlobConfig)

    # Web search configuration
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)

    # Web fetch configuration
    web_fetch: WebFetchConfig = field(default_factory=WebFetchConfig)

    # Memory system configuration
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)

    # Approval system
    approval: ApprovalConfig = field(default_factory=ApprovalConfig)

    # Hooks system
    hooks: HooksConfig = field(default_factory=HooksConfig)

    # Loop detection
    loop_detection: LoopDetectionConfig = field(default_factory=lambda: LoopDetectionConfig())

    # Session persistence & checkpoints
    session: SessionPersistenceConfig = field(default_factory=SessionPersistenceConfig)

    # Shortcuts for common access patterns
    @property
    def model(self) -> str:
        """Shortcut to get the model name."""
        return self.llm.model

    @property
    def api_key(self) -> str:
        """Shortcut to get the API key."""
        return self.llm.api_key

    @property
    def base_url(self) -> str:
        """Shortcut to get the base URL."""
        return self.llm.base_url

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors: list[str] = []

        if not self.llm.api_key:
            errors.append("LLM API key not set. Set LLM_API_KEY environment variable.")

        if not self.llm.base_url:
            errors.append("LLM base URL not set. Set LLM_BASE_URL environment variable.")

        if not self.llm.model:
            errors.append("LLM model not set. Set LLM_MODEL environment variable.")

        if self.cwd and not self.cwd.exists():
            errors.append(f"Working directory does not exist: {self.cwd}")

        return errors

    def update_from_dict(self, data: dict[str, Any]) -> None:
        """Merge a nested mapping into this config instance."""
        _deep_update(self, data)


def get_config() -> Config:
    """Get the singleton config instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def set_config(config: Config) -> None:
    """Set the singleton config instance."""
    global _config_instance
    _config_instance = config


def reset_config() -> None:
    """Reset the config instance (useful for testing)."""
    global _config_instance
    _config_instance = None

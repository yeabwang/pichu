"""Shared shell command risk classification utilities."""

from __future__ import annotations

import re

DANGEROUS_COMMAND_PATTERNS: tuple[str, ...] = (
    # File system destruction
    r"rm\s+(-rf?|--recursive)\s+[/~]",
    r"rm\s+-rf?\s+\*",
    r"rmdir\s+[/~]",
    # Disk operations
    r"dd\s+if=",
    r"mkfs",
    r"fdisk",
    r"parted",
    # System control
    r"shutdown",
    r"reboot",
    r"halt",
    r"poweroff",
    r"init\s+[06]",
    # Permission changes on root
    r"chmod\s+(-R\s+)?777\s+[/~]",
    r"chown\s+-R\s+.*\s+[/~]",
    # Network exposure
    r"nc\s+-l",
    r"netcat\s+-l",
    # Code execution fetched from network
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
    # Fork bomb
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;",
)

SAFE_COMMAND_PATTERNS: tuple[str, ...] = (
    # Information commands
    r"^(ls|dir|pwd|cd|echo|cat|head|tail|less|more|wc)(\s|$)",
    r"^(find|locate|which|whereis|file|stat)(\s|$)",
    # Development tools (read-only)
    r"^git\s+(status|log|diff|show|branch|remote|tag)(\s|$)",
    r"^(npm|yarn|pnpm)\s+(list|ls|outdated)(\s|$)",
    r"^pip\s+(list|show|freeze)(\s|$)",
    r"^cargo\s+(tree|search)(\s|$)",
    # Text processing
    r"^(grep|awk|sed|cut|sort|uniq|tr|diff|comm)(\s|$)",
    # System info
    r"^(date|cal|uptime|whoami|id|groups|hostname|uname)(\s|$)",
    r"^(env|printenv|set)$",
    # Process info
    r"^(ps|top|htop|pgrep)(\s|$)",
)


def normalize_command(command: str) -> str:
    """Normalize shell command text for matching and memory keys."""
    return " ".join(command.split())


def command_fingerprint(command: str) -> str:
    """Return a stable case-insensitive command fingerprint."""
    return normalize_command(command).casefold()


def is_dangerous_command(command: str, extra_contains: list[str] | None = None) -> bool:
    """Return True when a command matches high-risk signatures."""
    normalized = normalize_command(command)
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True

    if extra_contains:
        lowered = normalized.casefold()
        for marker in extra_contains:
            if marker and marker.casefold() in lowered:
                return True

    return False


def is_safe_command(command: str) -> bool:
    """Return True when a command looks read-only and low-risk."""
    normalized = normalize_command(command)
    for pattern in SAFE_COMMAND_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return True
    return False

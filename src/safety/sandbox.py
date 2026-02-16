"""Filesystem sandbox used by file-oriented tools."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from utils.runtime_logging import audit_event

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for filesystem sandbox behavior."""

    allowed_directories: list[str] = field(default_factory=list)
    restrict_to_cwd: bool = True
    allow_temp_writes: bool = False
    allow_executables: bool = False
    restrict_reads_to_allowed_dirs: bool = False
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
class SandboxResult:
    """Result of a sandbox policy check."""

    allowed: bool
    reason: str | None = None
    violation_type: Literal[
        "outside_allowed_dirs",
        "outside_read_scope",
        "blocked_pattern",
        "blocked_extension",
        "symlink_escape",
        None,
    ] = None


class FilesystemSandbox:
    """Sandbox that validates filesystem read/write targets."""

    def __init__(self, config: SandboxConfig | None = None, cwd: str | Path | None = None) -> None:
        self.config = config or SandboxConfig()
        self._cwd = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        self._allowed_dirs: list[Path] = []
        self._rebuild_allowed_dirs()

    @property
    def cwd(self) -> Path:
        return self._cwd

    def set_cwd(self, cwd: str | Path) -> None:
        self._cwd = Path(cwd).resolve()
        self._rebuild_allowed_dirs()

    def _rebuild_allowed_dirs(self) -> None:
        self._allowed_dirs = []

        if self.config.restrict_to_cwd:
            self._allowed_dirs.append(self._cwd)

        for directory in self.config.allowed_directories:
            self._allowed_dirs.append(Path(directory).resolve())

        if self.config.allow_temp_writes:
            import tempfile

            self._allowed_dirs.append(Path(tempfile.gettempdir()).resolve())

    def _resolve(self, path: str | Path) -> Path:
        raw = Path(path)
        return raw.resolve() if raw.is_absolute() else (self._cwd / raw).resolve()

    def _is_within_allowed_dirs(self, candidate: Path) -> bool:
        for allowed_dir in self._allowed_dirs:
            try:
                candidate.relative_to(allowed_dir)
                return True
            except ValueError:
                continue
        return False

    def _blocked(
        self,
        *,
        operation: str,
        path: str | Path,
        resolved: Path,
        reason: str,
        violation_type: Literal[
            "outside_allowed_dirs",
            "outside_read_scope",
            "blocked_pattern",
            "blocked_extension",
            "symlink_escape",
            None,
        ],
    ) -> SandboxResult:
        logger.warning("Sandbox blocked %s for %s: %s", operation, resolved, reason)
        audit_event(
            "sandbox.blocked",
            "Filesystem sandbox blocked operation",
            operation=operation,
            path=str(path),
            resolved_path=str(resolved),
            violation_type=violation_type,
            reason=reason,
        )
        return SandboxResult(
            allowed=False,
            reason=reason,
            violation_type=violation_type,
        )

    def can_write(self, path: str | Path) -> SandboxResult:
        resolved = self._resolve(path)
        resolved_lower = str(resolved).lower()

        for pattern in self.config.blocked_patterns:
            if pattern.lower() in resolved_lower:
                return self._blocked(
                    operation="write",
                    path=path,
                    resolved=resolved,
                    reason=f"Path matches blocked pattern: {pattern}",
                    violation_type="blocked_pattern",
                )

        suffix = resolved.suffix.lower()
        if suffix in self.config.blocked_extensions and not self.config.allow_executables:
            return self._blocked(
                operation="write",
                path=path,
                resolved=resolved,
                reason=f"Extension '{suffix}' is blocked for security",
                violation_type="blocked_extension",
            )

        if not self._is_within_allowed_dirs(resolved):
            allowed_str = ", ".join(str(d) for d in self._allowed_dirs[:3])
            if len(self._allowed_dirs) > 3:
                allowed_str += f" (+{len(self._allowed_dirs) - 3} more)"
            return self._blocked(
                operation="write",
                path=path,
                resolved=resolved,
                reason=f"Path '{resolved}' is outside allowed directories: {allowed_str}",
                violation_type="outside_allowed_dirs",
            )

        if resolved.exists() and resolved.is_symlink():
            real_path = resolved.resolve(strict=True)
            if not self._is_within_allowed_dirs(real_path):
                return self._blocked(
                    operation="write",
                    path=path,
                    resolved=resolved,
                    reason=f"Symlink '{path}' points outside allowed directories",
                    violation_type="symlink_escape",
                )

        return SandboxResult(allowed=True)

    def can_read(self, path: str | Path) -> SandboxResult:
        resolved = self._resolve(path)
        resolved_lower = str(resolved).lower()

        for pattern in self.config.sensitive_read_patterns:
            if pattern.lower() in resolved_lower:
                return self._blocked(
                    operation="read",
                    path=path,
                    resolved=resolved,
                    reason=f"Cannot read sensitive file matching pattern: {pattern}",
                    violation_type="blocked_pattern",
                )

        if self.config.restrict_reads_to_allowed_dirs and not self._is_within_allowed_dirs(resolved):
            allowed_str = ", ".join(str(d) for d in self._allowed_dirs[:3])
            if len(self._allowed_dirs) > 3:
                allowed_str += f" (+{len(self._allowed_dirs) - 3} more)"
            return self._blocked(
                operation="read",
                path=path,
                resolved=resolved,
                reason=f"Read path '{resolved}' is outside allowed directories: {allowed_str}",
                violation_type="outside_read_scope",
            )

        return SandboxResult(allowed=True)

    def validate_write_or_raise(self, path: str | Path) -> Path:
        result = self.can_write(path)
        if not result.allowed:
            raise ValueError(result.reason)
        return self._resolve(path)

    def get_allowed_directories(self) -> list[str]:
        return [str(d) for d in self._allowed_dirs]


_global_sandbox: FilesystemSandbox | None = None


def get_sandbox() -> FilesystemSandbox | None:
    return _global_sandbox


def set_sandbox(sandbox: FilesystemSandbox) -> None:
    global _global_sandbox
    _global_sandbox = sandbox


def reset_sandbox() -> None:
    global _global_sandbox
    _global_sandbox = None


def init_sandbox(cwd: str | Path, config: SandboxConfig | None = None) -> FilesystemSandbox:
    global _global_sandbox
    _global_sandbox = FilesystemSandbox(config=config, cwd=cwd)
    return _global_sandbox

"""Compatibility shim for sandbox imports.

New code should import from ``safety.sandbox`` directly.
"""

from safety.sandbox import (
    FilesystemSandbox,
    SandboxConfig,
    SandboxResult,
    get_sandbox,
    init_sandbox,
    reset_sandbox,
    set_sandbox,
)

__all__ = [
    "FilesystemSandbox",
    "SandboxConfig",
    "SandboxResult",
    "get_sandbox",
    "init_sandbox",
    "reset_sandbox",
    "set_sandbox",
]

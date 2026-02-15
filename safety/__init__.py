"""Safety subsystem public API.

The safety package centralizes guardrails used by tool execution:
- approval policies and rule evaluation
- command risk classification
- filesystem sandboxing
"""

from safety.approval import (
    ApprovalDecision,
    ApprovalManager,
    ApprovalResponse,
    is_dangerous_command,
    is_safe_command,
)
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
    "ApprovalDecision",
    "ApprovalManager",
    "ApprovalResponse",
    "FilesystemSandbox",
    "SandboxConfig",
    "SandboxResult",
    "get_sandbox",
    "init_sandbox",
    "reset_sandbox",
    "set_sandbox",
    "is_dangerous_command",
    "is_safe_command",
]

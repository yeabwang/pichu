"""Hook system for pichu.

Hooks are user-defined shell commands that execute at specific lifecycle
points, providing deterministic control over agent behavior.
"""

from hooks.engine import HookEngine, HookRegistration
from hooks.types import (
    HOOK_BLOCKING_EVENTS,
    HOOK_MATCHER_EVENTS,
    HookDecision,
    HookEvent,
    HookFireResult,
    HookHandler,
    HookInput,
    HookMatcher,
    HookResult,
)

__all__ = [
    "HookEvent",
    "HookHandler",
    "HookMatcher",
    "HookInput",
    "HookResult",
    "HookFireResult",
    "HookDecision",
    "HOOK_MATCHER_EVENTS",
    "HOOK_BLOCKING_EVENTS",
    "HookEngine",
    "HookRegistration",
]

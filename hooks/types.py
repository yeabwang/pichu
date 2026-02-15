"""Typed contracts for hook configuration and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """Lifecycle points where hooks can fire."""

    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    STOP = "Stop"
    SUBAGENT_STOP = "SubagentStop"
    NOTIFICATION = "Notification"
    PRE_COMPACT = "PreCompact"


HOOK_MATCHER_EVENTS = {
    HookEvent.SESSION_START,  # matches on: startup, resume, clear, compact
    HookEvent.SESSION_END,  # matches on: reason
    HookEvent.PRE_TOOL_USE,  # matches on: tool_name
    HookEvent.POST_TOOL_USE,  # matches on: tool_name
    HookEvent.POST_TOOL_USE_FAILURE,  # matches on: tool_name
    HookEvent.NOTIFICATION,  # matches on: notification_type
    HookEvent.PRE_COMPACT,  # matches on: trigger (manual/auto)
}

HOOK_BLOCKING_EVENTS = {
    HookEvent.PRE_TOOL_USE,  # can deny tool call
    HookEvent.USER_PROMPT_SUBMIT,  # can block prompt processing
    HookEvent.STOP,  # can force continuing
    HookEvent.SUBAGENT_STOP,  # can force sub-agent to continue
}

# Backward-compatible aliases used by existing imports/tests.
_MATCHER_EVENTS = HOOK_MATCHER_EVENTS
_BLOCKING_EVENTS = HOOK_BLOCKING_EVENTS


class HookDecision(str, Enum):
    """Decision outcome from a hook."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # escalate to user prompt (PreToolUse only)
    BLOCK = "block"  # generic block (UserPromptSubmit, PostToolUse, Stop)


@dataclass
class HookHandler:
    """A single hook handler (shell command to execute)."""

    type: str = "command"  # "command" (Phase 1), "prompt" (future)
    command: str = ""  # shell command to execute
    timeout: int = 60  # seconds before canceling
    async_: bool = False  # run in background without blocking
    status_message: str | None = None  # custom spinner message

    def __post_init__(self) -> None:
        if self.type not in ("command", "prompt", "agent"):
            raise ValueError(f"Invalid hook type: {self.type!r}")
        if self.type == "command" and not self.command:
            raise ValueError("Command hook must have a non-empty 'command'")


@dataclass
class HookMatcher:
    """A matcher group: regex pattern + list of hook handlers to run when matched."""

    matcher: str | None = None  # regex pattern, None = match all
    hooks: list[HookHandler] = field(default_factory=list)


@dataclass
class HookInput:
    """JSON input data sent to hook commands via stdin.

    Common fields are always present; event-specific fields are added
    based on the hook event type.
    """

    # Common fields (always present)
    session_id: str = ""
    transcript_path: str = ""  # path to conversation JSON (if available)
    cwd: str = ""
    hook_event_name: str = ""
    permission_mode: str = "on_request"

    # Event-specific fields (added dynamically)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """Convert to the JSON dict sent to hook commands."""
        data = {
            "session_id": self.session_id,
            "transcript_path": self.transcript_path,
            "cwd": self.cwd,
            "hook_event_name": self.hook_event_name,
            "permission_mode": self.permission_mode,
        }
        data.update(self.extra)
        return data


@dataclass
class HookResult:
    """Result from executing a single hook handler."""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    # --- Parsed fields from JSON stdout (populated on exit 0) ---

    # Universal fields
    continue_: bool = True  # if False, agent stops entirely
    stop_reason: str | None = None  # message shown to user when continue_=False
    suppress_output: bool = False  # hide stdout from verbose mode
    system_message: str | None = None  # warning shown to user
    additional_context: str | None = None  # context added to conversation

    # Decision control (event-specific)
    decision: str | None = None  # "block" for UserPromptSubmit/PostToolUse/Stop
    reason: str | None = None  # explanation for the decision

    # hookSpecificOutput (for PreToolUse, PermissionRequest, etc.)
    hook_specific_output: dict[str, Any] | None = None

    @property
    def is_success(self) -> bool:
        """Exit code 0 = success."""
        return self.exit_code == 0

    @property
    def is_blocking_error(self) -> bool:
        """Exit code 2 = blocking error."""
        return self.exit_code == 2

    @property
    def is_non_blocking_error(self) -> bool:
        """Any exit code other than 0 or 2 = non-blocking error."""
        return self.exit_code not in (0, 2) or self.timed_out

    # --- PreToolUse-specific convenience properties ---

    @property
    def permission_decision(self) -> str | None:
        """Get permissionDecision from hookSpecificOutput (PreToolUse)."""
        if self.hook_specific_output:
            return self.hook_specific_output.get("permissionDecision")
        return None

    @property
    def permission_decision_reason(self) -> str | None:
        """Get permissionDecisionReason from hookSpecificOutput (PreToolUse)."""
        if self.hook_specific_output:
            return self.hook_specific_output.get("permissionDecisionReason")
        return None

    @property
    def updated_input(self) -> dict[str, Any] | None:
        """Get updatedInput from hookSpecificOutput (PreToolUse)."""
        if self.hook_specific_output:
            return self.hook_specific_output.get("updatedInput")
        return None


@dataclass
class HookFireResult:
    """Aggregated result from firing all hooks for a single event."""

    event: HookEvent
    results: list[HookResult] = field(default_factory=list)

    @property
    def any_blocking(self) -> bool:
        """True if any hook returned a blocking error (exit 2)."""
        return any(r.is_blocking_error for r in self.results)

    @property
    def any_deny(self) -> bool:
        """True if any PreToolUse hook returned permissionDecision='deny'."""
        return any(r.permission_decision == "deny" for r in self.results)

    @property
    def any_allow(self) -> bool:
        """True if any PreToolUse hook returned permissionDecision='allow'."""
        return any(r.permission_decision == "allow" for r in self.results)

    @property
    def any_ask(self) -> bool:
        """True if any PreToolUse hook returned permissionDecision='ask'."""
        return any(r.permission_decision == "ask" for r in self.results)

    @property
    def should_block(self) -> bool:
        """True if any hook returned decision='block' or exit code 2."""
        if self.any_blocking:
            return True
        return any(r.decision == "block" for r in self.results)

    @property
    def should_stop(self) -> bool:
        """True if any hook set continue=False."""
        return any(not r.continue_ for r in self.results)

    @property
    def stop_reason(self) -> str | None:
        """First stop reason from any hook."""
        for r in self.results:
            if not r.continue_ and r.stop_reason:
                return r.stop_reason
        return None

    @property
    def blocking_stderr(self) -> str | None:
        """Stderr from the first blocking error (exit 2)."""
        for r in self.results:
            if r.is_blocking_error and r.stderr:
                return r.stderr.strip()
        return None

    @property
    def blocking_reason(self) -> str | None:
        """First reason from a blocking decision."""
        # From exit code 2 stderr
        if self.blocking_stderr:
            return self.blocking_stderr
        # From JSON decision.reason
        for r in self.results:
            if r.decision == "block" and r.reason:
                return r.reason
        # From PreToolUse deny reason
        for r in self.results:
            if r.permission_decision == "deny" and r.permission_decision_reason:
                return r.permission_decision_reason
        return None

    def collect_additional_context(self) -> str | None:
        """Collect all additionalContext from successful hooks."""
        contexts = []
        for r in self.results:
            if r.is_success and r.additional_context:
                contexts.append(r.additional_context)
        return "\n".join(contexts) if contexts else None

    def collect_system_messages(self) -> list[str]:
        """Collect all system messages from hooks."""
        return [r.system_message for r in self.results if r.system_message]

    @property
    def first_updated_input(self) -> dict[str, Any] | None:
        """Get the first updatedInput from PreToolUse hooks."""
        for r in self.results:
            if r.updated_input:
                return r.updated_input
        return None

    @property
    def any_suppress_output(self) -> bool:
        """True if any hook requested output suppression."""
        return any(r.suppress_output for r in self.results)

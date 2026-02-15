"""Event types and payload builders for the agent runtime stream."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from client.models import TokenUsage
from tools.base import ToolConfirmation, ToolResult


class AgentEventType(str, Enum):
    """Canonical event names for the agent runtime stream."""

    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"
    TEXT_DELTA = "text_delta"
    TEXT_COMPLETE = "text_complete"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_COMPLETE = "tool_call_complete"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    TASK_LIST_UPDATED = "task_list_updated"
    CONTEXT_COMPACTED = "context_compacted"
    CONTEXT_PRUNED = "context_pruned"
    CONTEXT_STATS = "context_stats"
    HOOK_FIRED = "hook_fired"
    HOOK_BLOCKED = "hook_blocked"
    THINKING_START = "thinking_start"
    THINKING_END = "thinking_end"
    LLM_RETRY = "llm_retry"
    EMPTY_RESPONSE = "empty_response"
    LOOP_DETECTED = "loop_detected"


@dataclass
class AgentEvent:
    """A typed runtime event consumed by CLI and TUI layers."""

    type: AgentEventType
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_start(cls, message: str) -> AgentEvent:
        """Create an agent-start event."""
        return cls(
            type=AgentEventType.AGENT_START,
            data={"message": message},
        )

    @classmethod
    def from_end(
        cls, response: str | None = None, usage: TokenUsage | None = None
    ) -> AgentEvent:
        """Create an agent-end event."""
        return cls(
            type=AgentEventType.AGENT_END,
            data={"message": response, "usage": usage.__dict__ if usage else None},
        )

    @classmethod
    def from_error(
        cls, error_message: str, details: dict[str, Any] | None = None
    ) -> AgentEvent:
        """Create an agent-error event."""
        return cls(
            type=AgentEventType.AGENT_ERROR,
            data={"error": error_message, "details": details},
        )

    @classmethod
    def from_text_delta(cls, content: str) -> AgentEvent:
        """Create a text-delta event."""
        return cls(
            type=AgentEventType.TEXT_DELTA,
            data={"content": content},
        )

    @classmethod
    def from_text_complete(
        cls, content: str, usage: TokenUsage | None = None
    ) -> AgentEvent:
        """Create a text-complete event."""
        return cls(
            type=AgentEventType.TEXT_COMPLETE,
            data={
                "content": content,
                "usage": usage.__dict__ if usage else None,
            },
        )

    @classmethod
    def tool_call_start(
        cls, call_id: str, name: str, arguments: dict[str, Any]
    ) -> AgentEvent:
        """Create a tool-call-start event."""
        return cls(
            type=AgentEventType.TOOL_CALL_START,
            data={
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            },
        )

    @classmethod
    def tool_call_complete(
        cls, call_id: str, name: str, result: ToolResult
    ) -> AgentEvent:
        """Create a tool-call-complete event."""
        diff_summary = None
        if result.diff:
            diff_summary = result.diff.create_diff_summary()
        elif result.batch_diff:
            diff_summary = result.batch_diff.create_diff_summary()

        return cls(
            type=AgentEventType.TOOL_CALL_COMPLETE,
            data={
                "call_id": call_id,
                "name": name,
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "metadata": result.metadata,
                "diff": diff_summary,
                "truncated": result.truncated,
                "exit_code": result.exit_code,
            },
        )

    @classmethod
    def task_created(
        cls,
        task_id: str,
        subject: str,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ) -> AgentEvent:
        """Create a task-created event."""
        return cls(
            type=AgentEventType.TASK_CREATED,
            data={
                "task_id": task_id,
                "subject": subject,
                "blocked_by": blocked_by or [],
                "blocks": blocks or [],
            },
        )

    @classmethod
    def task_started(
        cls,
        task_id: str,
        subject: str,
        active_form: str,
        owner: str | None = None,
    ) -> AgentEvent:
        """Create a task-started event."""
        return cls(
            type=AgentEventType.TASK_STARTED,
            data={
                "task_id": task_id,
                "subject": subject,
                "active_form": active_form,
                "owner": owner,
            },
        )

    @classmethod
    def task_completed(
        cls,
        task_id: str,
        subject: str,
        unblocked_tasks: list[str] | None = None,
    ) -> AgentEvent:
        """Create a task-completed event."""
        return cls(
            type=AgentEventType.TASK_COMPLETED,
            data={
                "task_id": task_id,
                "subject": subject,
                "unblocked_tasks": unblocked_tasks or [],
            },
        )

    @classmethod
    def task_updated(
        cls,
        task_id: str,
        updates: list[str],
    ) -> AgentEvent:
        """Create a task-updated event."""
        return cls(
            type=AgentEventType.TASK_UPDATED,
            data={
                "task_id": task_id,
                "updates": updates,
            },
        )

    @classmethod
    def task_deleted(cls, task_id: str, subject: str) -> AgentEvent:
        """Create a task-deleted event."""
        return cls(
            type=AgentEventType.TASK_DELETED,
            data={
                "task_id": task_id,
                "subject": subject,
            },
        )

    @classmethod
    def task_list_updated(
        cls,
        total: int,
        completed: int,
        in_progress: int,
        available: int,
        blocked: int,
    ) -> AgentEvent:
        """Create an aggregate task-list status event."""
        return cls(
            type=AgentEventType.TASK_LIST_UPDATED,
            data={
                "total": total,
                "completed": completed,
                "in_progress": in_progress,
                "available": available,
                "blocked": blocked,
            },
        )

    @classmethod
    def context_compacted(
        cls,
        original_tokens: int,
        new_tokens: int,
        summary_tokens: int,
    ) -> AgentEvent:
        """Create a context-compacted event."""
        return cls(
            type=AgentEventType.CONTEXT_COMPACTED,
            data={
                "original_tokens": original_tokens,
                "new_tokens": new_tokens,
                "summary_tokens": summary_tokens,
                "tokens_saved": original_tokens - new_tokens,
            },
        )

    @classmethod
    def context_pruned(
        cls,
        pruned_count: int,
        tokens_saved: int,
    ) -> AgentEvent:
        """Create a context-pruned event."""
        return cls(
            type=AgentEventType.CONTEXT_PRUNED,
            data={
                "pruned_count": pruned_count,
                "tokens_saved": tokens_saved,
            },
        )

    @classmethod
    def context_stats(
        cls,
        stats: dict,
    ) -> AgentEvent:
        """Create a context-stats event."""
        return cls(
            type=AgentEventType.CONTEXT_STATS,
            data=stats,
        )

    @classmethod
    def approval_requested(
        cls,
        confirmation: ToolConfirmation,
    ) -> AgentEvent:
        """Create an approval-requested event."""
        return cls(
            type=AgentEventType.APPROVAL_REQUESTED,
            data={
                "tool_name": confirmation.tool_name,
                "tool_kind": (
                    confirmation.tool_kind.value
                    if confirmation.tool_kind
                    else "unknown"
                ),
                "description": confirmation.description,
                "params": confirmation.params,
                "command": confirmation.command,
                "affected_paths": confirmation.affected_paths,
                "is_dangerous": confirmation.is_dangerous,
                "diff": (
                    confirmation.diff.create_diff_summary()
                    if confirmation.diff
                    else None
                ),
            },
        )

    @classmethod
    def approval_granted(
        cls,
        tool_name: str,
        remembered: bool = False,
    ) -> AgentEvent:
        """Create an approval-granted event."""
        return cls(
            type=AgentEventType.APPROVAL_GRANTED,
            data={
                "tool_name": tool_name,
                "remembered": remembered,
            },
        )

    @classmethod
    def approval_denied(
        cls,
        tool_name: str,
        reason: str = "user",
    ) -> AgentEvent:
        """Create an approval-denied event."""
        return cls(
            type=AgentEventType.APPROVAL_DENIED,
            data={
                "tool_name": tool_name,
                "reason": reason,
            },
        )

    @classmethod
    def thinking_start(cls, turn: int) -> AgentEvent:
        """Create a thinking-start event."""
        return cls(
            type=AgentEventType.THINKING_START,
            data={"turn": turn},
        )

    @classmethod
    def thinking_end(cls, turn: int) -> AgentEvent:
        """Create a thinking-end event."""
        return cls(
            type=AgentEventType.THINKING_END,
            data={"turn": turn},
        )

    @classmethod
    def llm_retry(
        cls, attempt: int, max_attempts: int, error: str, wait_seconds: float
    ) -> AgentEvent:
        """Create an LLM-retry event."""
        return cls(
            type=AgentEventType.LLM_RETRY,
            data={
                "attempt": attempt,
                "max_attempts": max_attempts,
                "error": error,
                "wait_seconds": wait_seconds,
            },
        )

    @classmethod
    def empty_response(cls, turn: int) -> AgentEvent:
        """Create an empty-response event."""
        return cls(
            type=AgentEventType.EMPTY_RESPONSE,
            data={"turn": turn},
        )

    @classmethod
    def loop_detected(
        cls,
        strategy: str,
        description: str,
        turn_count: int,
        action_taken: str,
    ) -> AgentEvent:
        """Create a loop-detected event."""
        return cls(
            type=AgentEventType.LOOP_DETECTED,
            data={
                "strategy": strategy,
                "description": description,
                "turn_count": turn_count,
                "action_taken": action_taken,
            },
        )

    @classmethod
    def hook_fired(
        cls,
        event_name: str,
        handler_count: int,
        match_value: str | None = None,
    ) -> AgentEvent:
        """Create a hook-fired event."""
        return cls(
            type=AgentEventType.HOOK_FIRED,
            data={
                "event_name": event_name,
                "handler_count": handler_count,
                "match_value": match_value,
            },
        )

    @classmethod
    def hook_blocked(
        cls,
        event_name: str,
        reason: str,
        tool_name: str | None = None,
    ) -> AgentEvent:
        """Create a hook-blocked event."""
        return cls(
            type=AgentEventType.HOOK_BLOCKED,
            data={
                "event_name": event_name,
                "reason": reason,
                "tool_name": tool_name,
            },
        )

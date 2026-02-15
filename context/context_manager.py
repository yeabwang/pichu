"""Conversation context state and token accounting for the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from client.models import TokenUsage
from config import Config
from prompts.system import get_system_prompt
from utils.text import count_tokens

if TYPE_CHECKING:
    from tools.base import Tool


PRUNED_TOOL_OUTPUT_PLACEHOLDER = "[Old tool result content cleared]"

SUMMARY_RESTORE_PROMPT = """# Context Restoration (Compacted Conversation)

The prior conversation was compacted due to context limits.
Continue from this summary and avoid repeating completed work.

---

{summary}

---

Proceed with the next unfinished step."""

SUMMARY_ACK_MESSAGE = """Context summary received.
I will continue from the remaining work and avoid repeating completed actions."""

SUMMARY_CONTINUE_PROMPT = "Continue with the remaining work from the summary above."


@dataclass
class MessageItem:
    """Internal conversation item stored in context history."""

    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    token_count: int | None = None
    pruned_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageItem:
        """Create a message item from serialized storage payload."""
        pruned_raw = data.get("pruned_at")
        pruned_at = (
            datetime.fromisoformat(pruned_raw)
            if isinstance(pruned_raw, str) and pruned_raw
            else None
        )
        return cls(
            role=data["role"],
            content=data.get("content", "") or "",
            tool_call_id=data.get("tool_call_id"),
            tool_calls=data.get("tool_calls", []) or [],
            token_count=data.get("token_count"),
            pruned_at=pruned_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this message item into provider-compatible payload shape."""
        result: dict[str, Any] = {"role": self.role}

        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.content is not None:
            result["content"] = self.content
        return result


class ContextManager:
    """Owns in-memory context messages and token usage snapshots."""

    def __init__(self, config: Config, tools: list["Tool"] | None = None) -> None:
        self._config = config
        self._system_prompt = get_system_prompt(self._config, tools=tools)
        self._model_name = self._config.model
        self._messages: list[MessageItem] = []
        self._latest_usage: TokenUsage = TokenUsage()
        self._total_usage: TokenUsage = TokenUsage()

    def _count_message_tokens(self, content: str) -> int:
        return count_tokens(text=content, model=self._model_name)

    def _append_message(self, item: MessageItem) -> MessageItem:
        self._messages.append(item)
        return item

    def get_user_message(self, content: str) -> MessageItem:
        """Append a user message to context history."""
        return self._append_message(
            MessageItem(
                role="user",
                content=content,
                token_count=self._count_message_tokens(content),
            )
        )

    def get_agent_message(
        self, content: str | None, tool_calls: list[dict[str, Any]] | None = None
    ) -> MessageItem:
        """Append an assistant message to context history."""
        content = content or ""
        return self._append_message(
            MessageItem(
                role="assistant",
                content=content,
                token_count=self._count_message_tokens(content),
                tool_calls=tool_calls or [],
            )
        )

    def get_messages(self) -> list[dict[str, Any]]:
        """Build provider payload messages including the system prompt."""
        messages = []

        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})

        for msg in self._messages:
            messages.append(msg.to_dict())

        return messages

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Append a tool role message generated from one tool execution."""
        self._append_message(
            MessageItem(
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                token_count=self._count_message_tokens(content),
            )
        )

    def set_latest_usage(self, usage: TokenUsage) -> None:
        """Set token usage from the most recent model response."""
        self._latest_usage = usage

    def add_usage(self, usage: TokenUsage) -> None:
        """Accumulate total token usage across this session."""
        self._total_usage += usage

    def get_latest_usage(self) -> TokenUsage:
        """Return usage for the latest model response."""
        return self._latest_usage

    def get_total_usage(self) -> TokenUsage:
        """Return cumulative usage tracked for this session."""
        return self._total_usage

    def get_message_token_total(self) -> int:
        """Return total tokens represented by in-memory message items."""
        total = 0
        for message in self._messages:
            total += message.token_count or self._count_message_tokens(message.content)
        return total

    def get_current_token_count(self) -> int:
        """Return current prompt token estimate based on latest usage snapshot."""
        if self._latest_usage.total_tokens > 0:
            return self._latest_usage.total_tokens
        system_tokens = (
            self._count_message_tokens(self._system_prompt)
            if self._system_prompt
            else 0
        )
        return system_tokens + self.get_message_token_total()

    def get_context_stats(self, tool_schema_tokens: int = 0) -> dict:
        """Return a token-usage breakdown for debug and UI rendering."""
        context_limit = self._config.limits.context_window
        total_tokens = self.get_current_token_count()

        system_tokens = (
            self._count_message_tokens(self._system_prompt)
            if self._system_prompt
            else 0
        )
        user_tokens = 0
        assistant_tokens = 0
        tool_result_tokens = 0

        for msg in self._messages:
            tokens = msg.token_count or self._count_message_tokens(msg.content)
            if msg.role == "user":
                user_tokens += tokens
            elif msg.role == "assistant":
                assistant_tokens += tokens
            elif msg.role == "tool":
                tool_result_tokens += tokens

        percentage = (total_tokens / context_limit * 100) if context_limit > 0 else 0

        return {
            "total_tokens": total_tokens,
            "context_limit": context_limit,
            "percentage": round(percentage, 1),
            "system_tokens": system_tokens,
            "tool_def_tokens": tool_schema_tokens,
            "user_tokens": user_tokens,
            "assistant_tokens": assistant_tokens,
            "tool_result_tokens": tool_result_tokens,
        }

    def needs_compression(self) -> bool:
        """Return whether context has crossed the configured compression threshold."""
        context_limit = self._config.limits.context_window
        current_tokens = self.get_current_token_count()
        threshold = self._config.limits.compression_threshold

        return current_tokens > (context_limit * threshold)

    def replace_with_summary(self, summary: str) -> None:
        """Replace conversation history with compacted continuation prompts."""
        self._messages = []

        summary_prompt = SUMMARY_RESTORE_PROMPT.format(summary=summary)
        self._append_message(
            MessageItem(
                role="user",
                content=summary_prompt,
                token_count=self._count_message_tokens(summary_prompt),
            )
        )
        self._append_message(
            MessageItem(
                role="assistant",
                content=SUMMARY_ACK_MESSAGE,
                token_count=self._count_message_tokens(SUMMARY_ACK_MESSAGE),
            )
        )
        self._append_message(
            MessageItem(
                role="user",
                content=SUMMARY_CONTINUE_PROMPT,
                token_count=self._count_message_tokens(SUMMARY_CONTINUE_PROMPT),
            )
        )

    def prune_tool_outputs(self) -> int:
        """Prune old tool output payloads while preserving recent execution context."""
        pruned_count, _ = self.prune_tool_outputs_with_stats()
        return pruned_count

    def prune_tool_outputs_with_stats(self) -> tuple[int, int]:
        """Prune old tool payloads and return `(count_pruned, tokens_saved)`."""
        user_message_count = sum(1 for msg in self._messages if msg.role == "user")

        if user_message_count < 2:
            return 0, 0

        total_tokens = 0
        pruned_tokens = 0
        to_prune: list[MessageItem] = []

        for msg in reversed(self._messages):
            if msg.role == "tool" and msg.tool_call_id:
                if msg.pruned_at:
                    break

                tokens = msg.token_count or self._count_message_tokens(msg.content)
                total_tokens += tokens

                if total_tokens > self._config.limits.prune_protect_tokens:
                    pruned_tokens += tokens
                    to_prune.append(msg)

        if pruned_tokens < self._config.limits.prune_minimum_tokens:
            return 0, 0

        pruned_count = 0
        tokens_saved = 0

        for msg in to_prune:
            original_tokens = msg.token_count or self._count_message_tokens(msg.content)
            msg.content = PRUNED_TOOL_OUTPUT_PLACEHOLDER
            msg.token_count = self._count_message_tokens(msg.content)
            msg.pruned_at = datetime.now()
            tokens_saved += max(0, original_tokens - msg.token_count)
            pruned_count += 1

        return pruned_count, tokens_saved

    def clear(self) -> None:
        """Clear in-memory conversation history for this session."""
        self._messages = []

    @property
    def message_count(self) -> int:
        """Return number of in-memory context messages."""
        return len(self._messages)

    def serialize_messages(self) -> list[dict[str, Any]]:
        """Serialize all context items for persistence."""
        result: list[dict[str, Any]] = []
        for msg in self._messages:
            d: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                d["content"] = msg.content
            if msg.tool_call_id is not None:
                d["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
            if msg.token_count is not None:
                d["token_count"] = msg.token_count
            if msg.pruned_at is not None:
                d["pruned_at"] = msg.pruned_at.isoformat()
            result.append(d)
        return result

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        """Load serialized context messages (used by resume/rewind flows)."""
        self._messages = [MessageItem.from_dict(message) for message in messages]

    def truncate_to(self, message_index: int) -> None:
        """Truncate context history to `[0:message_index]`."""
        self._messages = self._messages[:message_index]

"""Typed contracts shared by the LLM client and the agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any

JSONDict = dict[str, Any]


@dataclass
class TextDelta:
    """Represents a streamed text fragment from the model."""

    content: str

    def __str__(self) -> str:
        return self.content


class StreamEventType(str, Enum):
    """Event kinds emitted by :class:`client.llm_client.LLMClient`."""

    TEXT_DELTA = "text_delta"
    MESSAGE_COMPLETE = "message_complete"
    ERROR = "error"

    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_COMPLETE = "tool_call_complete"


@dataclass
class TokenUsage:
    """Token accounting returned by model providers."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cache_tokens=self.cache_tokens + other.cache_tokens,
        )


@dataclass
class ToolCallDelta:
    """Incremental tool-call payload emitted during streaming."""

    call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


@dataclass
class ToolCall:
    """Normalized tool call payload consumed by the agent loop."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    """Single unit in the client streaming protocol."""

    type: StreamEventType
    data: JSONDict | None = None
    text_delta: TextDelta | None = None
    error: str | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    tool_call_delta: ToolCallDelta | None = None
    tool_call: ToolCall | None = None

    @classmethod
    def from_text(cls, content: str) -> StreamEvent:
        """Create a text delta event."""
        return cls(
            type=StreamEventType.TEXT_DELTA,
            text_delta=TextDelta(content=content),
        )

    @classmethod
    def from_finish(
        cls,
        finish_reason: str | None,
        usage: TokenUsage | None = None,
        text_delta: TextDelta | None = None,
    ) -> StreamEvent:
        """Create a completion event with optional usage and final text."""
        return cls(
            type=StreamEventType.MESSAGE_COMPLETE,
            data={
                "finish_reason": finish_reason,
            },
            finish_reason=finish_reason,
            text_delta=text_delta,
            usage=usage,
        )

    @classmethod
    def from_error(cls, error_message: str) -> StreamEvent:
        """Create a normalized error event."""
        return cls(
            type=StreamEventType.ERROR,
            data={"error": error_message},
            error=error_message,
        )


@dataclass
class ToolResultMessage:
    """Tool-result payload persisted into conversation history."""

    tool_call_id: str
    content: str
    is_error: bool = False

    def to_openai_schema(self) -> JSONDict:
        """Return the OpenAI-compatible `tool` role message payload."""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }

    def to_open_ai_schema(self) -> dict[str, Any]:
        """Backward-compatible alias for older call sites."""
        return self.to_openai_schema()


def parse_tool_call_arguments(arguments_str: str) -> dict[str, Any]:
    """Parse JSON tool arguments, preserving raw input on malformed payloads."""
    if not arguments_str:
        return {}

    try:
        parsed = json.loads(arguments_str)
    except json.JSONDecodeError:
        return {"raw_arguments": arguments_str}

    if isinstance(parsed, dict):
        return parsed
    return {"raw_arguments": arguments_str}

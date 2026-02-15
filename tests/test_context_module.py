"""Regression tests for context management and compaction behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from client.models import StreamEvent, TextDelta, TokenUsage
from config import Config
from context.compaction import ChatCompactor
from context.context_manager import (
    PRUNED_TOOL_OUTPUT_PLACEHOLDER,
    ContextManager,
)


def test_context_manager_tracks_messages_and_usage():
    cfg = Config()
    manager = ContextManager(cfg)

    manager.get_user_message("hello")
    manager.get_agent_message(
        "I can help.",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
    )
    manager.add_tool_result("call_1", "file content")

    usage = TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    manager.set_latest_usage(usage)
    manager.add_usage(usage)

    messages = manager.get_messages()
    assert messages[0]["role"] == "system"
    assert [message["role"] for message in messages[1:]] == [
        "user",
        "assistant",
        "tool",
    ]
    assert manager.get_current_token_count() == 18
    assert manager.get_total_usage().total_tokens == 18


def test_prune_tool_outputs_with_stats_prunes_old_results():
    cfg = Config()
    cfg.limits.prune_protect_tokens = 0
    cfg.limits.prune_minimum_tokens = 1

    manager = ContextManager(cfg)
    manager.get_user_message("first user message")
    manager.add_tool_result("call_a", "A " * 1000)
    manager.get_user_message("second user message")
    manager.add_tool_result("call_b", "B " * 1000)

    pruned_count, tokens_saved = manager.prune_tool_outputs_with_stats()
    assert pruned_count >= 1
    assert tokens_saved >= 0

    serialized = manager.serialize_messages()
    assert any(
        message.get("role") == "tool" and message.get("content") == PRUNED_TOOL_OUTPUT_PLACEHOLDER
        for message in serialized
    )


class _DummyLLMClient:
    def __init__(self) -> None:
        self.last_messages: list[dict] | None = None

    def count_tokens(self, messages: list[dict]) -> int:
        self.last_messages = messages
        return 999

    async def chat_completion(self, messages: list[dict], stream: bool = False):
        self.last_messages = messages
        yield StreamEvent.from_finish(
            "stop",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
            text_delta=TextDelta(content="Compacted summary"),
        )


@pytest.mark.asyncio
async def test_chat_compactor_uses_focus_prompt_and_returns_summary():
    cfg = Config()
    manager = ContextManager(cfg)
    manager.get_user_message("Please help me refactor this module.")

    dummy_client = _DummyLLMClient()
    compactor = ChatCompactor(dummy_client, cfg)
    summary, usage, original_tokens = await compactor.compact_context(
        context_manager=manager,
        max_tokens=100,
        system_prompt="Focus on architecture and testing coverage.",
    )

    assert summary == "Compacted summary"
    assert usage is not None and usage.total_tokens == 28
    assert original_tokens == 999
    assert dummy_client.last_messages is not None
    assert "Focus on architecture and testing coverage." in dummy_client.last_messages[0]["content"]

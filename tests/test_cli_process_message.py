"""Tests for CLI message processing behavior."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.events import AgentEvent
from main import CLI


@dataclass
class _DummyTUI:
    context_updates: list[dict] = field(default_factory=list)

    def begin_agent_response(self) -> None:
        return None

    def stream_agent_delta(self, content: str) -> None:
        return None

    def end_agent_response(self) -> None:
        return None

    def stop_thinking(self) -> None:
        return None

    def show_empty_response(self) -> None:
        return None

    def show_loop_warning(self, strategy: str, description: str, turn_count: int, action_taken: str) -> None:
        return None

    def show_llm_retry(self, attempt: int, max_attempts: int, error: str, wait_seconds: float) -> None:
        return None

    def print_context_event(self, event_type: str, details: dict) -> None:
        return None

    def print_context_stats(self, stats: dict) -> None:
        return None

    def update_context_tracker(self, stats: dict) -> None:
        self.context_updates.append(stats)

    def print_approval_denied(self, tool_name: str, reason: str) -> None:
        return None

    def tool_call_start(self, call_id: str, name: str, tool_kind: str | None, arguments: dict) -> None:
        return None

    def tool_call_complete(
        self,
        call_id: str,
        name: str,
        tool_kind: str | None,
        output: str,
        success: bool,
        diff,
        error: str | None,
        metadata: dict | None = None,
        truncated: bool = False,
        exit_code: int | None = None,
    ) -> None:
        return None

    def start_thinking(self) -> None:
        return None


class _DummyAgent:
    async def run(self, prompt: str):
        yield AgentEvent.from_text_delta("hello")
        yield AgentEvent.from_text_complete("hello")


class _DummyAgentWithContextStats:
    async def run(self, prompt: str):
        stats = {"total_tokens": 10, "context_limit": 100, "percentage": 10.0}
        yield AgentEvent.context_stats(stats)
        yield AgentEvent.from_text_complete("ok")


@pytest.mark.asyncio
async def test_process_message_returns_text_response():
    cli = CLI.__new__(CLI)
    cli.agent = _DummyAgent()
    cli.tui = _DummyTUI()
    cli._get_tool_kind = lambda _name: "unknown"

    response = await cli._process_message("ping")
    assert response == "hello"


@pytest.mark.asyncio
async def test_process_message_updates_context_tracker_from_context_stats_event():
    cli = CLI.__new__(CLI)
    cli.agent = _DummyAgentWithContextStats()
    cli.tui = _DummyTUI()
    cli._get_tool_kind = lambda _name: "unknown"

    response = await cli._process_message("ping")
    assert response == "ok"
    assert cli.tui.context_updates == [{"total_tokens": 10, "context_limit": 100, "percentage": 10.0}]

"""Phase 4 runtime background execution tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from commands.base import CommandDisplayPayload
from main import CLI
from ui.runtime.task_runner import (
    RuntimeEventType,
    RuntimeStatusEvent,
    RuntimeTaskCancelledError,
    RuntimeTaskResult,
    RuntimeTaskRunner,
)


@pytest.mark.asyncio
async def test_runtime_task_runner_cancelled_task_emits_cancelled_without_completed() -> None:
    runner = RuntimeTaskRunner()

    async def _run(context) -> RuntimeTaskResult:
        for _ in range(50):
            await asyncio.sleep(0.01)
            context.raise_if_cancelled()
        return RuntimeTaskResult(output="done")

    handle = runner.submit("slow-operation", _run)

    observed_events: list[RuntimeStatusEvent] = []
    for _ in range(20):
        observed_events.extend(runner.drain_events())
        if any(event.event_type == RuntimeEventType.STARTED for event in observed_events):
            break
        await asyncio.sleep(0.01)

    handle.cancel("user-requested")
    with pytest.raises(RuntimeTaskCancelledError):
        await handle.result()

    observed_events.extend(runner.drain_events())
    event_types = [event.event_type for event in observed_events]
    assert RuntimeEventType.CANCELLED in event_types
    assert RuntimeEventType.COMPLETED not in event_types


class _ToolRenderTUI:
    def __init__(self) -> None:
        self.runtime_event_types: list[RuntimeEventType] = []
        self.render_calls: list[list[object]] = []
        self.cleared_call_ids: list[str] = []

    def update_runtime_status(self, event: RuntimeStatusEvent) -> None:
        self.runtime_event_types.append(event.event_type)

    def prepare_tool_call_complete_payload(self, **_kwargs):
        return CommandDisplayPayload(renderables=["tool-panel"])

    def render_command_payload(self, renderables: list[object]) -> None:
        self.render_calls.append(renderables)

    def get_tool_call_arguments_snapshot(self, _call_id: str) -> dict:
        return {"command": "echo hi"}

    def clear_tool_call_arguments(self, call_id: str) -> None:
        self.cleared_call_ids.append(call_id)

    def tool_call_complete(self, *args, **kwargs) -> None:
        raise AssertionError("fallback render path should not be used")


@pytest.mark.asyncio
async def test_tool_completion_runtime_path_routes_status_and_completed_payload() -> None:
    cli = CLI.__new__(CLI)
    cli._runtime_runner = RuntimeTaskRunner()
    cli.tui = _ToolRenderTUI()

    await cli._run_tool_call_complete_with_runtime_runner(
        call_id="call-1234",
        name="bash",
        tool_kind="shell",
        output="hello",
        success=True,
        diff=None,
        error=None,
        metadata=None,
        truncated=False,
        exit_code=0,
    )

    assert cli.tui.render_calls == [["tool-panel"]]
    assert cli.tui.cleared_call_ids == ["call-1234"]
    assert RuntimeEventType.PROGRESS in cli.tui.runtime_event_types
    assert RuntimeEventType.COMPLETED in cli.tui.runtime_event_types


class _IdleInputRunner:
    def __init__(self, event: RuntimeStatusEvent) -> None:
        self._event = event
        self._emitted = False
        self.drain_calls = 0

    def drain_events(self):
        self.drain_calls += 1
        if self._emitted:
            return []
        self._emitted = True
        return [self._event]


class _IdleInputTUI:
    def __init__(self) -> None:
        self.runtime_event_types: list[RuntimeEventType] = []

    async def get_user_input(self, prompt: str = "❯") -> str:
        await asyncio.sleep(0.12)
        return prompt

    def update_runtime_status(self, event: RuntimeStatusEvent) -> None:
        self.runtime_event_types.append(event.event_type)


@pytest.mark.asyncio
async def test_get_user_input_drains_runtime_events_while_idle() -> None:
    cli = CLI.__new__(CLI)
    cli.tui = _IdleInputTUI()
    cli._runtime_runner = _IdleInputRunner(
        RuntimeStatusEvent(
            task_id="rt-1",
            operation="background",
            event_type=RuntimeEventType.PROGRESS,
            message="working",
            progress_current=1,
            progress_total=2,
        )
    )

    value = await cli._get_user_input_with_runtime_drain()
    assert value == "❯"
    assert cli.tui.runtime_event_types == [RuntimeEventType.PROGRESS]
    assert cli._runtime_runner.drain_calls >= 1

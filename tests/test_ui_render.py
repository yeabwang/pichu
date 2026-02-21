from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.output import renderer as renderer_module
from ui.output.renderer import DifferentialRichRenderer, RenderState, RuntimeProgressState

_TEST_THEME = Theme({"agent": "white", "streaming": "white"})


class _DummyLive:
    def __init__(self) -> None:
        self.update_calls = 0
        self.last_text = ""
        self.stop_calls = 0

    def update(self, renderable, refresh: bool = False) -> None:
        self.update_calls += 1
        if isinstance(renderable, Text):
            self.last_text = renderable.plain
        else:
            self.last_text = str(renderable)

    def stop(self) -> None:
        self.stop_calls += 1


def test_footer_refresh_is_debounced(monkeypatch):
    now = {"t": 100.0}
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: now["t"])

    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer._footer_enabled = True
    dummy_live = _DummyLive()
    renderer._footer_live = dummy_live

    renderer.update_task_footer({"in_progress": 1, "available": 3, "blocked": 1, "completed": 2})
    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 0

    now["t"] += 0.08
    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 1
    assert dummy_live.last_text == "Tasks: in-progress 1 • ready 3 • blocked 1 • done 2"


def test_print_renderable_does_not_force_footer_refresh(monkeypatch):
    now = {"t": 200.0}
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: now["t"])

    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer._footer_enabled = True
    dummy_live = _DummyLive()
    renderer._footer_live = dummy_live

    renderer.update_task_footer({"in_progress": 2, "available": 1, "blocked": 0, "completed": 4})
    now["t"] += 0.08  # Debounce deadline passed

    renderer.print_renderable(Text("tool panel"))
    assert dummy_live.update_calls == 0

    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 1


def test_footer_refresh_waits_for_stream_buffer_to_flush(monkeypatch):
    now = {"t": 300.0}
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: now["t"])

    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer._footer_enabled = True
    dummy_live = _DummyLive()
    renderer._footer_live = dummy_live

    renderer.update_task_footer({"in_progress": 1, "available": 0, "blocked": 0, "completed": 0})
    now["t"] += 0.08
    renderer.state.stream_buffer = "partial delta"

    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 0

    renderer.state.stream_buffer = ""
    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 1


def test_footer_refresh_waits_until_stream_ends(monkeypatch):
    now = {"t": 400.0}
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: now["t"])

    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer._footer_enabled = True
    dummy_live = _DummyLive()
    renderer._footer_live = dummy_live

    renderer.update_task_footer({"in_progress": 1, "available": 0, "blocked": 0, "completed": 0})
    now["t"] += 0.08
    renderer._agent_stream_active = True
    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 0

    renderer._agent_stream_active = False
    renderer.flush_deferred_updates()
    assert dummy_live.update_calls == 1


def test_runtime_progress_text_uses_active_label():
    state = RuntimeProgressState(active=2, queued=1, progress_current=1, progress_total=3, last_message="working")
    text = state.to_text()
    assert text.startswith("active 2")
    assert "bg active" not in text


def test_begin_agent_response_marks_stream_active():
    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer.begin_agent_response()

    assert renderer._agent_stream_active is True


def test_end_agent_response_clears_stream_active():
    renderer = DifferentialRichRenderer(Console(file=io.StringIO(), force_terminal=False), RenderState())
    renderer._agent_stream_active = True
    renderer.end_agent_response()

    assert renderer._agent_stream_active is False


def test_command_payload_and_agent_rule_have_single_blank_line_separator():
    output = io.StringIO()
    renderer = DifferentialRichRenderer(
        Console(file=output, force_terminal=False, width=100, theme=_TEST_THEME, highlight=False),
        RenderState(),
    )

    renderer.render_command_payload(["", "Conversation history cleared. Starting fresh.", ""])
    renderer.begin_agent_response()
    renderer.stream_agent_delta("hello")
    renderer.end_agent_response()

    lines = output.getvalue().splitlines()
    message_idx = next(i for i, line in enumerate(lines) if "Conversation history cleared." in line)
    rule_idx = next(i for i, line in enumerate(lines) if "Agent" in line)

    assert message_idx == 0
    assert rule_idx - message_idx == 2
    assert lines[rule_idx - 1] == ""


def test_agent_then_command_payload_have_single_blank_line_separator():
    output = io.StringIO()
    renderer = DifferentialRichRenderer(
        Console(file=output, force_terminal=False, width=100, theme=_TEST_THEME, highlight=False),
        RenderState(),
    )

    renderer.begin_agent_response()
    renderer.stream_agent_delta("first response")
    renderer.end_agent_response()
    renderer.render_command_payload(["command panel"])

    lines = output.getvalue().splitlines()
    agent_text_idx = next(i for i, line in enumerate(lines) if "first response" in line)
    command_idx = next(i for i, line in enumerate(lines) if "command panel" in line)

    assert command_idx - agent_text_idx == 2
    assert lines[command_idx - 1] == ""


def test_consecutive_agent_rules_have_single_blank_line_between_blocks():
    output = io.StringIO()
    renderer = DifferentialRichRenderer(
        Console(file=output, force_terminal=False, width=100, theme=_TEST_THEME, highlight=False),
        RenderState(),
    )

    renderer.begin_agent_response()
    renderer.stream_agent_delta("first response")
    renderer.end_agent_response()
    renderer.begin_agent_response()
    renderer.stream_agent_delta("second response")
    renderer.end_agent_response()

    lines = output.getvalue().splitlines()
    rule_indices = [i for i, line in enumerate(lines) if "Agent" in line]
    first_response_idx = next(i for i, line in enumerate(lines) if "first response" in line)

    assert len(rule_indices) == 2
    assert rule_indices[1] - first_response_idx == 2
    assert lines[rule_indices[1] - 1] == ""


def test_command_payload_then_prompt_has_single_blank_line_separator():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=100, theme=_TEST_THEME, highlight=False)
    renderer = DifferentialRichRenderer(console, RenderState())

    renderer.render_command_payload(["command panel"])
    renderer.begin_prompt_block()
    console.print("❯ user input")

    lines = output.getvalue().splitlines()
    command_idx = next(i for i, line in enumerate(lines) if "command panel" in line)
    prompt_idx = next(i for i, line in enumerate(lines) if "❯ user input" in line)

    assert prompt_idx - command_idx == 2
    assert lines[prompt_idx - 1] == ""


def test_prompt_then_agent_rule_has_single_blank_line_separator():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, width=100, theme=_TEST_THEME, highlight=False)
    renderer = DifferentialRichRenderer(console, RenderState())

    renderer.begin_prompt_block()
    console.print("❯ user input")
    renderer.begin_agent_response()
    renderer.stream_agent_delta("answer")
    renderer.end_agent_response()

    lines = output.getvalue().splitlines()
    prompt_idx = next(i for i, line in enumerate(lines) if "❯ user input" in line)
    rule_idx = next(i for i, line in enumerate(lines) if "Agent" in line)

    assert rule_idx - prompt_idx == 2
    assert lines[rule_idx - 1] == ""

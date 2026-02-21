from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from ui.capabilities.detection import is_color_supported, is_dumb_terminal, is_interactive_tty
from ui.runtime.task_runner import RuntimeEventType, RuntimeStatusEvent
from ui.tui import TUI


class _TTYStream:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_is_interactive_tty_requires_stdin_and_stdout(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _TTYStream(True))
    monkeypatch.setattr(sys, "stdout", _TTYStream(True))
    assert is_interactive_tty() is True

    monkeypatch.setattr(sys, "stdout", _TTYStream(False))
    assert is_interactive_tty() is False

    monkeypatch.setattr(sys, "stdin", _TTYStream(False))
    monkeypatch.setattr(sys, "stdout", _TTYStream(True))
    assert is_interactive_tty() is False


def test_color_capabilities_respect_no_color_and_dumb_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("COLORTERM", raising=False)
    assert is_color_supported() is True
    assert is_dumb_terminal() is False

    monkeypatch.setenv("NO_COLOR", "1")
    assert is_color_supported() is False

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert is_dumb_terminal() is True
    assert is_color_supported() is False


def test_non_tty_does_not_instantiate_prompt_session(monkeypatch, tmp_path):
    from ui import tui as tui_module
    from ui.input import manager as input_manager_module

    monkeypatch.setattr(tui_module, "is_interactive_tty", lambda: False)

    def _explode_prompt_session(*args, **kwargs):
        raise AssertionError("PromptSession should not be created in non-TTY mode")

    monkeypatch.setattr(input_manager_module, "PromptSession", _explode_prompt_session)
    tui = TUI(console=Console(file=io.StringIO(), force_terminal=False), config=Config(cwd=tmp_path))
    assert tui._input_manager is None


def test_non_tty_does_not_activate_rich_live(monkeypatch, tmp_path):
    from ui import tui as tui_module
    from ui.controller import event_router as event_router_module
    from ui.output import renderer as renderer_module

    monkeypatch.setattr(tui_module, "is_interactive_tty", lambda: False)

    class _ExplodeLive:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Live should not be created in non-TTY mode")

    monkeypatch.setattr(event_router_module, "Live", _ExplodeLive)
    monkeypatch.setattr(renderer_module, "Live", _ExplodeLive)

    tui = TUI(console=Console(file=io.StringIO(), force_terminal=False), config=Config(cwd=tmp_path))
    tui.start_thinking()
    assert tui._thinking_live is None

    tui.update_task_footer({"in_progress": 1, "available": 0, "blocked": 0, "completed": 0})
    tui.flush_deferred_ui(force=True)
    assert tui._renderer is not None
    assert tui._renderer._footer_live is None


def test_tui_footer_updates_refresh_prompt_footer_if_input_manager_present(monkeypatch, tmp_path):
    from ui import tui as tui_module

    monkeypatch.setattr(tui_module, "is_interactive_tty", lambda: False)
    tui = TUI(console=Console(file=io.StringIO(), force_terminal=False), config=Config(cwd=tmp_path))

    class _InputManagerStub:
        def __init__(self) -> None:
            self.refresh_calls = 0

        def refresh_footer(self) -> None:
            self.refresh_calls += 1

    stub = _InputManagerStub()
    tui._input_manager = stub
    tui.update_task_footer({"in_progress": 1, "available": 0, "blocked": 0, "completed": 0})
    tui.update_runtime_status(RuntimeStatusEvent(task_id="rt-1", operation="bg", event_type=RuntimeEventType.QUEUED))
    assert stub.refresh_calls == 2

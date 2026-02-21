"""Tests for CLI message processing behavior."""

from __future__ import annotations

import asyncio
import io
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
    task_footer_updates: list[dict] = field(default_factory=list)
    streamed_chunks: list[str] = field(default_factory=list)
    agent_running_updates: list[bool] = field(default_factory=list)

    def begin_agent_response(self) -> None:
        return None

    def stream_agent_delta(self, content: str) -> None:
        self.streamed_chunks.append(content)

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

    def update_task_footer(self, stats: dict) -> None:
        self.task_footer_updates.append(stats)

    def flush_deferred_ui(self, force: bool = False) -> None:
        return None

    def set_agent_running(self, is_agent_running: bool) -> None:
        self.agent_running_updates.append(is_agent_running)

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


class _DummyAgentWithTaskListUpdate:
    async def run(self, prompt: str):
        yield AgentEvent.task_list_updated(total=7, completed=2, in_progress=1, available=3, blocked=1)
        yield AgentEvent.from_text_complete("ok")


class _DummyAgentWithInterrupt:
    def __init__(self, cli: CLI):
        self._cli = cli

    async def run(self, prompt: str):
        yield AgentEvent.from_text_delta("first")
        self._cli._interrupt_event.set()
        yield AgentEvent.from_text_delta("second")
        yield AgentEvent.from_text_complete("done")


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


@pytest.mark.asyncio
async def test_process_message_updates_task_footer_from_task_list_event():
    cli = CLI.__new__(CLI)
    cli.agent = _DummyAgentWithTaskListUpdate()
    cli.tui = _DummyTUI()
    cli._get_tool_kind = lambda _name: "unknown"

    response = await cli._process_message("ping")
    assert response == "ok"
    assert cli.tui.task_footer_updates == [{"total": 7, "completed": 2, "in_progress": 1, "available": 3, "blocked": 1}]


@pytest.mark.asyncio
async def test_process_message_task_list_event_updates_footer_render_state(tmp_path):
    from rich.console import Console

    from config.config import Config
    from ui.tui import AGENT_THEME, TUI

    cli = CLI.__new__(CLI)
    cli.agent = _DummyAgentWithTaskListUpdate()
    cli.tui = TUI(
        console=Console(file=io.StringIO(), force_terminal=False, width=100, theme=AGENT_THEME, highlight=False),
        config=Config(cwd=tmp_path),
    )
    cli._get_tool_kind = lambda _name: "unknown"

    response = await cli._process_message("ping")
    assert response == "ok"
    assert cli.tui._render_state.task_footer.to_text() == "Tasks: in-progress 1 • ready 3 • blocked 1 • done 2"


@pytest.mark.asyncio
async def test_process_message_interrupt_event_stops_stream_and_prints_interrupted(monkeypatch):
    import pichu_main as main_module

    cli = CLI.__new__(CLI)
    cli._interrupt_event = asyncio.Event()
    cli.tui = _DummyTUI()
    cli.agent = _DummyAgentWithInterrupt(cli)
    cli._get_tool_kind = lambda _name: "unknown"
    cli._runtime_runner = None

    printed: list[str] = []
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: printed.append(str(args[0])))

    response = await cli._process_message("ping")

    assert response is None
    assert cli.tui.streamed_chunks == ["first"]
    assert cli.tui.agent_running_updates == [True, False]
    assert not cli._interrupt_event.is_set()
    assert any("Interrupted." in line for line in printed)


def test_config_has_llm_model_detects_model_entry(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[llm]\nmodel = "openai/gpt-4.1"\n', encoding="utf-8")
    assert CLI._config_has_llm_model(config_path)


def test_is_model_configured_false_without_env_or_config_model(tmp_path, monkeypatch):
    project_dir = tmp_path / ".pichu"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text("[llm]\ntimeout = 120.0\n", encoding="utf-8")
    system_config = tmp_path / "system.toml"
    system_config.write_text("[llm]\nbase_url = 'https://example.com'\n", encoding="utf-8")

    import pichu_main as main_module

    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("PICHU_PROJECT_DIR", ".pichu")
    monkeypatch.setenv("PICHU_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(main_module, "get_system_config_path", lambda: system_config)

    cli = CLI.__new__(CLI)
    cli._config = type("Cfg", (), {"cwd": tmp_path})()

    assert not cli._is_model_configured()


def test_ensure_workspace_trust_denies_untrusted_workspace(monkeypatch, tmp_path):
    import pichu_main as main_module

    cli = CLI.__new__(CLI)
    cli._config = type("Cfg", (), {"cwd": tmp_path})()

    class _TrustPromptTUI:
        def __init__(self):
            self.goodbye_message = None

        def prompt_workspace_trust(self, _workspace):
            return False

        def print_goodbye(self, message: str):
            self.goodbye_message = message

    cli.tui = _TrustPromptTUI()

    monkeypatch.setattr(main_module, "is_workspace_trusted", lambda _workspace: False)
    monkeypatch.setattr(main_module, "trust_workspace", lambda _workspace: True)

    assert not cli._ensure_workspace_trust(prompt_if_needed=True)
    assert cli.tui.goodbye_message == "Workspace not trusted. Exiting for safety."


def test_ensure_workspace_trust_persists_acceptance(monkeypatch, tmp_path):
    import pichu_main as main_module

    cli = CLI.__new__(CLI)
    cli._config = type("Cfg", (), {"cwd": tmp_path})()

    class _TrustPromptTUI:
        def prompt_workspace_trust(self, _workspace):
            return True

        def print_goodbye(self, _message: str):
            raise AssertionError("goodbye should not be called for trusted workspace")

    cli.tui = _TrustPromptTUI()

    called = {"saved": False}

    monkeypatch.setattr(main_module, "is_workspace_trusted", lambda _workspace: False)

    def _record_trust(_workspace):
        called["saved"] = True
        return True

    monkeypatch.setattr(main_module, "trust_workspace", _record_trust)

    assert cli._ensure_workspace_trust(prompt_if_needed=True)
    assert called["saved"]


def test_ensure_workspace_trust_non_interactive_untrusted(monkeypatch, tmp_path):
    import pichu_main as main_module

    cli = CLI.__new__(CLI)
    cli._config = type("Cfg", (), {"cwd": tmp_path})()

    class _TrustPromptTUI:
        def prompt_workspace_trust(self, _workspace):
            raise AssertionError("prompt should not be shown in non-interactive mode")

        def print_goodbye(self, _message: str):
            raise AssertionError("goodbye should not be printed for non-interactive refusal")

    cli.tui = _TrustPromptTUI()

    monkeypatch.setattr(main_module, "is_workspace_trusted", lambda _workspace: False)
    monkeypatch.setattr(main_module, "trust_workspace", lambda _workspace: True)
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: None)

    assert not cli._ensure_workspace_trust(prompt_if_needed=False)


def test_clear_terminal_if_supported_calls_console_clear_for_interactive_tty():
    cli = CLI.__new__(CLI)

    class _Console:
        def __init__(self) -> None:
            self.clear_calls = 0

        def clear(self) -> None:
            self.clear_calls += 1

    console = _Console()
    cli.tui = type("TUIStub", (), {"_interactive_tty": True, "console": console})()

    cli._clear_terminal_if_supported()

    assert console.clear_calls == 1


def test_clear_terminal_if_supported_is_noop_for_non_interactive_tty():
    cli = CLI.__new__(CLI)

    class _Console:
        def __init__(self) -> None:
            self.clear_calls = 0

        def clear(self) -> None:
            self.clear_calls += 1

    console = _Console()
    cli.tui = type("TUIStub", (), {"_interactive_tty": False, "console": console})()

    cli._clear_terminal_if_supported()

    assert console.clear_calls == 0


def test_clear_terminal_if_supported_is_noop_when_console_lacks_clear():
    cli = CLI.__new__(CLI)
    cli.tui = type("TUIStub", (), {"_interactive_tty": True, "console": object()})()

    cli._clear_terminal_if_supported()

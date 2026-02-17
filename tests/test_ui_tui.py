"""Focused tests for TUI formatting helpers."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from ui.tui import TUI


def _make_tui(tmp_path: Path) -> TUI:
    cfg = Config(cwd=tmp_path)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)
    return TUI(console=console, config=cfg)


def test_context_tracker_summary_uses_latest_stats(tmp_path):
    tui = _make_tui(tmp_path)
    assert tui.get_context_tracker_summary() == "0 / 128.0K tokens • 0.0%"

    tui.update_context_tracker({"total_tokens": 1530, "context_limit": 2048, "percentage": 74.7})
    assert tui.get_context_tracker_summary() == "1.5K / 2.0K tokens • 74.7%"


def test_ordered_tool_arguments_prioritizes_known_fields(tmp_path):
    tui = _make_tui(tmp_path)
    ordered = tui._ordered_tool_arguments(
        "shell",
        {"cwd": "/tmp", "z": "last", "command": "echo hi", "timeout": 30},
    )

    assert ordered == [
        ("command", "echo hi"),
        ("timeout", 30),
        ("cwd", "/tmp"),
        ("z", "last"),
    ]


def test_extract_read_file_code_parses_line_numbered_output(tmp_path):
    tui = _make_tui(tmp_path)
    extracted = tui._extract_read_file_code("Showing lines: 10-11 of 50\n\n10: first\n11: second")
    assert extracted == (10, "10: first\n11: second")


def test_prompt_workspace_trust_renders_message_and_returns_selection(tmp_path, monkeypatch):
    tui = _make_tui(tmp_path)
    monkeypatch.setattr(tui, "_interactive_select", lambda options, default_index=0: options[0][0])

    assert tui.prompt_workspace_trust(tmp_path)
    output = tui.console.file.getvalue()
    assert "Accessing workspace:" in output
    assert "read, edit, and execute files" in output

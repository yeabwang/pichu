from __future__ import annotations

import sys
from pathlib import Path

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode

sys.path.insert(0, str(Path(__file__).parent.parent))

from commands import create_command_router
from ui.input.manager import (
    PROMPT_STYLE_RULES,
    CommandChoice,
    RoutedInputCompleter,
    SlashFuzzyCompleter,
    find_active_at_token_start,
    resolve_editing_mode,
)


def test_resolve_editing_mode_supports_vi_and_emacs():
    assert resolve_editing_mode("vi") == EditingMode.VI
    assert resolve_editing_mode("emacs") == EditingMode.EMACS
    assert resolve_editing_mode("unknown") == EditingMode.EMACS


def test_slash_fuzzy_completer_returns_fuzzy_command_matches():
    completer = SlashFuzzyCompleter(
        [
            CommandChoice(name="help", description="Show command help"),
            CommandChoice(name="status", description="Show status"),
            CommandChoice(name="sessions", description="List sessions"),
        ]
    )

    completions = list(
        completer.get_completions(
            Document("/hlp"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert any(item.text == "/help" for item in completions)


def test_slash_fuzzy_completer_uses_aliases():
    completer = SlashFuzzyCompleter(
        [
            CommandChoice(name="login", description="Configure model", aliases=("auth",)),
        ]
    )
    completions = list(
        completer.get_completions(
            Document("/aut"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert completions
    assert completions[0].text == "/login"


def test_slash_fuzzy_completer_only_triggers_for_slash_prefix():
    completer = SlashFuzzyCompleter(
        [
            CommandChoice(name="help", description="Show command help"),
        ]
    )
    completions = list(
        completer.get_completions(
            Document("hlp"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert completions == []


def test_slash_fuzzy_completer_supports_all_registered_commands():
    _, registry = create_command_router()
    specs = registry.specs(include_hidden=False)
    completer = SlashFuzzyCompleter(
        [CommandChoice(name=spec.name, description=spec.description, aliases=spec.aliases) for spec in specs]
    )
    completions = list(
        completer.get_completions(
            Document("/"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert len({item.text for item in completions}) == 29


def test_prompt_completion_style_matches_agent_palette():
    assert PROMPT_STYLE_RULES["completion-menu"] == "bg:#282c34 #7f848e"
    assert PROMPT_STYLE_RULES["completion-menu.completion"] == "bg:#282c34 #61afef"
    assert PROMPT_STYLE_RULES["completion-menu.meta.completion"] == "bg:#282c34 #5c6370"
    assert PROMPT_STYLE_RULES["completion-menu.completion.current"] == "bg:#61afef #282c34"
    assert PROMPT_STYLE_RULES["completion-menu.meta.completion.current"] == "bg:#3e4451 #d7dae0"
    assert PROMPT_STYLE_RULES["fuzzymatch.inside.character"] == "bold underline"


def test_find_active_at_token_start_handles_mid_sentence_at_path():
    assert find_active_at_token_start("fix the bug in @src/ui/") == (15, "src/ui/")


def test_routed_completer_routes_slash_inputs_to_command_completer():
    _, registry = create_command_router()
    specs = registry.specs(include_hidden=False)
    completer = RoutedInputCompleter(
        commands=[CommandChoice(name=spec.name, description=spec.description, aliases=spec.aliases) for spec in specs]
    )

    completions = list(
        completer.get_completions(
            Document("/hlp"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert any(item.text == "/help" for item in completions)


def test_routed_completer_routes_mid_sentence_at_to_path_completer(tmp_path):
    ui_dir = tmp_path / "src" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "renderer.py").write_text("", encoding="utf-8")
    completer = RoutedInputCompleter(cwd=tmp_path)

    completions = list(
        completer.get_completions(
            Document("fix the bug in @src/ui/"),
            CompleteEvent(completion_requested=True),
        )
    )

    assert any(item.text == "@src/ui/renderer.py" for item in completions)


def test_routed_completer_routes_at_start_of_input_to_path_completer(tmp_path):
    (tmp_path / "alpha.txt").write_text("", encoding="utf-8")
    completer = RoutedInputCompleter(cwd=tmp_path)

    completions = list(
        completer.get_completions(
            Document("@a"),
            CompleteEvent(completion_requested=True),
        )
    )

    assert any(item.text == "@alpha.txt" for item in completions)


def test_routed_completer_plain_text_has_no_completions(tmp_path):
    completer = RoutedInputCompleter(cwd=tmp_path)
    completions = list(
        completer.get_completions(
            Document("just normal text"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert completions == []

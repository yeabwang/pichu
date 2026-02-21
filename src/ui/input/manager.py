from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, FuzzyCompleter, PathCompleter, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style

from ui.contracts.models import CommandChoice
from ui.theme import (
    PROMPT_CONTINUATION_ANSI,
    PROMPT_FOOTER_ANSI,
    PROMPT_STYLE_RULES,
    PROMPT_SYMBOL_ANSI,
)


def resolve_editing_mode(raw_mode: str | None) -> EditingMode:
    normalized = (raw_mode or "").strip().lower()
    if normalized == "vi":
        return EditingMode.VI
    return EditingMode.EMACS


_AT_TOKEN_PATTERN = re.compile(r"(?:^|\s)@(?P<path>[^\s@]*)$")


def find_active_at_token_start(text_before_cursor: str) -> tuple[int, str] | None:
    match = _AT_TOKEN_PATTERN.search(text_before_cursor)
    if not match:
        return None
    path_start = match.start("path")
    return path_start - 1, match.group("path")


class SlashFuzzyCompleter(Completer):
    def __init__(self, commands: Iterable[CommandChoice] | None = None) -> None:
        self._commands: list[CommandChoice] = []
        self._variant_to_command: dict[str, str] = {}
        self._command_meta: dict[str, str] = {}
        self._delegate: Completer = FuzzyCompleter(WordCompleter([]))
        self.set_commands(commands or [])

    def set_commands(self, commands: Iterable[CommandChoice]) -> None:
        normalized: list[CommandChoice] = []
        for command in commands:
            name = command.name.strip().lstrip("/").lower()
            if not name:
                continue
            aliases = tuple(alias.strip().lstrip("/").lower() for alias in command.aliases if alias.strip())
            normalized.append(
                CommandChoice(
                    name=name,
                    description=command.description,
                    aliases=aliases,
                )
            )
        self._commands = sorted(normalized, key=lambda c: c.name)

        words: list[str] = []
        meta_by_word: dict[str, str] = {}
        variant_to_command: dict[str, str] = {}
        command_meta: dict[str, str] = {}

        for command in self._commands:
            canonical = f"/{command.name}"
            command_meta[canonical] = command.description
            for variant in (command.name, *command.aliases):
                word = f"/{variant}"
                if word in variant_to_command:
                    continue
                words.append(word)
                variant_to_command[word] = canonical
                if command.description:
                    meta_by_word[word] = command.description

        self._variant_to_command = variant_to_command
        self._command_meta = command_meta
        self._delegate = FuzzyCompleter(
            WordCompleter(
                sorted(words),
                ignore_case=True,
                match_middle=True,
                sentence=False,
                meta_dict=meta_by_word,
            )
        )

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or any(char.isspace() for char in text[1:]):
            return

        seen: set[str] = set()
        for completion in self._delegate.get_completions(document, complete_event):
            command_name = self._variant_to_command.get(completion.text, completion.text)
            if command_name in seen:
                continue
            seen.add(command_name)
            yield Completion(
                command_name,
                start_position=completion.start_position,
                display=command_name,
                display_meta=self._command_meta.get(command_name, ""),
            )


class RoutedInputCompleter(Completer):
    def __init__(self, cwd: Path | None = None, commands: Iterable[CommandChoice] | None = None) -> None:
        self._slash_completer = SlashFuzzyCompleter(commands=commands)
        base_cwd = cwd.resolve() if cwd else Path.cwd().resolve()
        self._path_completer = PathCompleter(get_paths=lambda: [str(base_cwd)], expanduser=False)

    def set_commands(self, commands: Iterable[CommandChoice]) -> None:
        self._slash_completer.set_commands(commands)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            yield from self._slash_completer.get_completions(document, complete_event)
            return

        active_token = find_active_at_token_start(text)
        if active_token is None:
            return

        _at_start, path_prefix = active_token
        path_document = Document(text=path_prefix, cursor_position=len(path_prefix))
        replace_span = -(len(path_prefix) + 1)
        for completion in self._path_completer.get_completions(path_document, complete_event):
            completed_path = f"@{path_prefix}{completion.text}"
            display = f"@{path_prefix}{completion.display_text}"
            yield Completion(
                completed_path,
                start_position=replace_span,
                display=display,
                display_meta=completion.display_meta,
                style=completion.style,
                selected_style=completion.selected_style,
            )


class InputManager:
    def __init__(
        self,
        history_path: Path | None = None,
        footer_provider: Callable[[], str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._completer = RoutedInputCompleter(cwd=cwd)
        self._footer_provider = footer_provider
        self._interrupt_handler: Callable[[], None] | None = None
        self.is_agent_running = False
        key_mode = resolve_editing_mode(os.environ.get("AGENT_UI_KEYMAP"))
        key_bindings = KeyBindings()
        interrupt_filter = Condition(lambda: self.is_agent_running and self._interrupt_handler is not None)

        @key_bindings.add("enter")
        def _submit_enter(event) -> None:
            event.current_buffer.validate_and_handle()

        @key_bindings.add("escape", "enter")
        def _insert_newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @key_bindings.add("c-s")
        def _submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @key_bindings.add("escape", filter=interrupt_filter)
        def _interrupt_agent(event) -> None:
            if self._interrupt_handler:
                self._interrupt_handler()
            event.current_buffer.reset()

        history = FileHistory(str(history_path)) if history_path else InMemoryHistory()
        self._session: PromptSession[str] = PromptSession(
            multiline=True,
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            style=Style.from_dict(PROMPT_STYLE_RULES),
            auto_suggest=AutoSuggestFromHistory(),
            completer=self._completer,
            history=history,
            editing_mode=key_mode,
            key_bindings=key_bindings,
            prompt_continuation=lambda width, line_number, wrap_count: ANSI(f"{PROMPT_CONTINUATION_ANSI}… \x1b[0m"),
            bottom_toolbar=self._render_bottom_toolbar if self._footer_provider else None,
        )

    def set_commands(self, commands: Iterable[CommandChoice]) -> None:
        self._completer.set_commands(commands)

    def set_footer_provider(self, footer_provider: Callable[[], str] | None) -> None:
        self._footer_provider = footer_provider

    def refresh_footer(self) -> None:
        app = getattr(self._session, "app", None)
        if app is None or not getattr(app, "is_running", False):
            return
        app.invalidate()

    def set_interrupt_handler(self, interrupt_handler: Callable[[], None] | None) -> None:
        self._interrupt_handler = interrupt_handler

    def set_agent_running(self, is_agent_running: bool) -> None:
        self.is_agent_running = is_agent_running

    def _render_bottom_toolbar(self):
        if self._footer_provider is None:
            return ""
        text = self._footer_provider().strip()
        if not text:
            return ""
        return ANSI(f"{PROMPT_FOOTER_ANSI}{text}\x1b[0m")

    async def prompt_async(self, symbol: str = "❯") -> str:
        prompt_text = ANSI(f"{PROMPT_SYMBOL_ANSI}{symbol}\x1b[0m ")
        with patch_stdout(raw=True):
            return await self._session.prompt_async(prompt_text)

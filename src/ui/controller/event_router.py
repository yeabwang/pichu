from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from tools.base import FileDiff
from ui.theme import SYNTAX_HIGHLIGHT_THEME
from utils.paths import display_path_relative_to_cwd
from utils.text import truncate_text_to_token_limit

if TYPE_CHECKING:
    from config import Config
    from safety.approval import ApprovalResponse
    from tools.base import ToolConfirmation
    from ui.output.renderer import DifferentialRichRenderer


@runtime_checkable
class TUIProtocol(Protocol):
    console: Console
    _renderer: "DifferentialRichRenderer"
    _config: "Config | None"
    _tool_args_by_call_id: dict[str, dict[str, Any]]
    _thinking_live: Live | None
    _latest_context_stats: dict[str, Any] | None
    _agent_stream_open: bool
    _interactive_tty: bool


@lru_cache(maxsize=1)
def _load_lexers() -> dict[str, str]:
    lexers_file = Path(__file__).parent.parent / "lexers.json"
    try:
        with open(lexers_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


class TUIEventRouterMixin(TUIProtocol):
    console: Console
    _renderer: "DifferentialRichRenderer"
    _config: "Config | None"
    _tool_args_by_call_id: dict[str, dict[str, Any]]
    _thinking_live: Live | None
    _latest_context_stats: dict[str, Any] | None
    _agent_stream_open: bool
    _interactive_tty: bool

    @property
    def _model(self) -> str:
        """Get model name from config, with safe default."""
        return self._config.model if self._config else ""

    def start_thinking(self) -> None:
        """Show a spinner while waiting for the LLM to respond."""
        if not getattr(self, "_interactive_tty", False):
            return
        self.stop_thinking()  # Ensure no leftover
        spinner = Spinner("dots", text=Text(" Thinking...", style="thinking"), style="spinner")
        self._thinking_live = Live(spinner, console=self.console, refresh_per_second=10, transient=True)
        self._thinking_live.start()

    def stop_thinking(self) -> None:
        """Stop the thinking spinner."""
        if self._thinking_live:
            self._thinking_live.stop()
            self._thinking_live = None

    def show_llm_retry(self, attempt: int, max_attempts: int, error: str, wait_seconds: float) -> None:
        """Show a retry warning when the LLM call fails."""
        self.stop_thinking()
        self.console.print(
            f"  [warning]⟳ LLM retry {attempt}/{max_attempts}[/warning] "
            f"[dim]({error})[/dim] "
            f"[dim italic]waiting {wait_seconds:.0f}s...[/dim italic]"
        )

    def show_empty_response(self) -> None:
        """Warn user when the LLM returned nothing."""
        self.stop_thinking()
        self.console.print(
            "\n  [warning]⚠ LLM returned an empty response[/warning] "
            "[dim](model may be overloaded — try again or switch models)[/dim]"
        )

    def show_loop_warning(
        self,
        strategy: str,
        description: str,
        turn_count: int,
        action_taken: str,
    ) -> None:
        """Show a warning when the loop detector fires."""
        self.stop_thinking()
        action_label = {
            "warn_and_nudge": "nudging agent to try a different approach",
            "pause": "pausing for your input",
            "stop": "stopping the agent",
        }.get(action_taken, action_taken)

        self.console.print(
            Panel(
                f"[warning]Strategy:[/warning] {strategy}\n"
                f"[dim]{description}[/dim]\n"
                f"[warning]Turns involved:[/warning] {turn_count}  │  "
                f"[warning]Action:[/warning] {action_label}",
                title="[warning]⚠ Loop Detected[/warning]",
                border_style="warning",
                padding=(0, 2),
            )
        )

    @property
    def cwd(self) -> Path:
        """Get working directory from config or use current directory."""
        if self._config:
            return self._config.cwd
        return Path.cwd()

    @property
    def tool_output_display_tokens(self) -> int:
        """Get display token limit from config or use default."""
        if self._config:
            return self._config.limits.tool_output_display_tokens
        return 2000  # fallback

    @property
    def tool_output_preview_tokens(self) -> int:
        """Get preview token limit from config or use default."""
        if self._config:
            return self._config.limits.tool_output_preview_tokens
        return 200  # fallback

    def update_context_tracker(self, stats: dict[str, Any]) -> None:
        """Store latest context stats for compact tracker display."""
        self._latest_context_stats = stats

    def get_context_tracker_summary(self) -> str:
        """Return compact context summary text for status areas."""

        def fmt(n: int) -> str:
            if n >= 1000:
                return f"{n / 1000:.1f}K"
            return str(n)

        stats = self._latest_context_stats or {}
        total = int(stats.get("total_tokens", 0))
        default_limit = self._config.limits.context_window if self._config else 0
        limit = int(stats.get("context_limit", default_limit))
        pct = float(stats.get("percentage", 0.0))
        return f"{fmt(total)} / {fmt(limit)} tokens • {pct:.1f}%"

    def begin_agent_response(self) -> None:
        self.stop_thinking()
        self._renderer.begin_agent_response()
        self._agent_stream_open = True

    def end_agent_response(self) -> None:
        if self._agent_stream_open:
            self._renderer.end_agent_response()
        self._agent_stream_open = False

    def stream_agent_delta(self, content: str) -> None:
        self._renderer.stream_agent_delta(content)

    def update_task_footer(self, stats: dict[str, Any]) -> None:
        self._renderer.update_task_footer(stats)

    def update_runtime_status(self, event: Any) -> None:
        self._renderer.update_runtime_status(event)

    def flush_deferred_ui(self, *, force: bool = False) -> None:
        self._renderer.flush_deferred_updates(force=force)

    def render_command_payload(self, renderables: list[Any]) -> None:
        self._renderer.render_command_payload(renderables)

    def get_tool_call_arguments_snapshot(self, call_id: str) -> dict[str, Any]:
        return dict(self._tool_args_by_call_id.get(call_id, {}))

    def clear_tool_call_arguments(self, call_id: str) -> None:
        self._tool_args_by_call_id.pop(call_id, None)

    def _ordered_tool_arguments(self, tool: str, args: dict[str, Any]) -> list[tuple]:
        _PREFERRED_ORDER = {
            "read_file": ["file_path", "offset", "limit"],
            "write_file": ["file_path", "create_directories", "content"],
            "edit_file": [
                "file_path",
                "replace_all",
                "old_string",
                "new_string",
            ],
            "shell": ["command", "timeout", "cwd"],
            "list_dir": ["path", "depth", "limit", "include_hidden"],
            "grep": ["pattern", "path", "include", "context_lines", "limit"],
            "glob": ["pattern", "path", "limit", "include_hidden", "files_only"],
            "web_search": ["query", "max_results", "mode"],
            "web_fetch": ["action", "url", "pattern", "mode"],
        }

        preferred = _PREFERRED_ORDER.get(tool, [])
        ordered: list[tuple[str, Any]] = []
        remaining_args = dict(args)  # Make a copy to avoid mutating original
        for key in preferred:
            if key in remaining_args:
                ordered.append((key, remaining_args.pop(key)))

        remaining = sorted(remaining_args.items())
        ordered.extend(remaining)
        return ordered

    def _render_args_table(self, tool_name: str, arguments: dict[str, Any]) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(justify="right", style="tool.arg", no_wrap=True)
        table.add_column(style="code", overflow="fold")

        for key, value in self._ordered_tool_arguments(tool_name, arguments):
            table.add_row(f"{key}:", str(value))

        return table

    def tool_call_start(self, call_id: str, name: str, tool_kind: str | None, arguments: dict[str, Any]) -> None:
        self._tool_args_by_call_id[call_id] = arguments
        # Use known tool kinds or fallback to "unknown"
        known_kinds = {"read", "write", "shell", "network", "memory", "mcp"}
        if tool_kind and tool_kind in known_kinds:
            border_style = f"tool.{tool_kind}"
        else:
            border_style = "tool.unknown"

        title = Text.assemble(
            ("✺ ", "tool.arg"),
            (name, "tool.name"),
            (" ", "tool.arg"),
            (f"#{call_id[:8]}", "tool.arg"),
        )

        display_args = dict(arguments)  # Copy to avoid mutation

        # Shorten large content fields (show metadata instead of full content)
        for key in ("content", "old_string", "new_string"):
            val = display_args.get(key)
            if isinstance(val, str):
                line_count = len(val.splitlines())
                byte_count = len(val.encode("utf-8", errors="replace"))
                display_args[key] = f"<{line_count} lines, {byte_count} bytes>"

        # Hide edits array for batch_apply (shown in diff on completion)
        if name == "batch_apply" and "edits" in display_args:
            edit_count = len(display_args["edits"]) if isinstance(display_args["edits"], list) else 0
            display_args["edits"] = f"<{edit_count} edit(s)>"

        # Convert paths to relative for display
        for key in ("path", "cwd", "file_path"):
            val = display_args.get(key)
            if isinstance(val, str) and self.cwd:
                display_args[key] = display_path_relative_to_cwd(val, self.cwd)

        panel = Panel(
            renderable=(
                self._render_args_table(name, display_args) if display_args else Text("No args", style="tool.arg")
            ),
            title=title,
            padding=(1, 2),
            box=box.ROUNDED,
            border_style=border_style,
            subtitle=Text("Running...", style="pending"),
            title_align="left",
            subtitle_align="right",
        )
        self._renderer.print_renderable(panel, with_leading_blank=False)

    def _extract_read_file_code(self, text: str) -> tuple[int, str] | None:
        body = text
        header_match = re.match(r"^Showing lines: (\d+)-(\d+) of (\d+)\n\n", text)
        if header_match:
            body = text[header_match.end() :]

        code_lines: list[str] = []
        start_line: int | None = None

        for line in body.splitlines():
            line_match = re.match(r"^\s*(\d+):\s?(.*)$", line)
            if not line_match:
                return None
            line_num = int(line_match.group(1))
            if start_line is None:
                start_line = line_num
            code_lines.append(line_match.group(0).strip())

        if start_line is not None:
            return start_line, "\n".join(code_lines)
        else:
            return None

    def _guess_language(self, path: str | None) -> str:
        if path is None:
            return "text"
        suffix = Path(path).suffix.lower()
        return _load_lexers().get(suffix, "text")

    def prepare_tool_call_complete_payload(
        self,
        *,
        call_id: str,
        name: str,
        tool_kind: str | None,
        output: str,
        success: bool,
        diff: FileDiff | None,
        error: str | None,
        metadata: dict[str, Any] | None = None,
        truncated: bool = False,
        exit_code: int | None = None,
        arguments: dict[str, Any] | None = None,
    ):
        from commands.base import CommandDisplayPayload

        # suppressOutput: hook requested hiding this tool's output
        if isinstance(metadata, dict) and metadata.get("suppress_output"):
            rendered = Text.assemble(
                (" ✓ " if success else " ✗ ", "success" if success else "error"),
                (name, "tool.name"),
                (" ", ""),
                ("(output suppressed by hook)", "dim"),
            )
            return CommandDisplayPayload(renderables=[rendered])

        border_style = f"tool.{tool_kind}" if tool_kind else "tool"
        status_icon = " ✓ " if success else " ✗ "
        status_style = "success" if success else "error"

        title = Text.assemble(
            (f"{status_icon}", f"{status_style}"),
            (name, "tool.name"),
            (" ", "tool.arg"),
            (f"#{call_id[:8]}", "tool.arg"),
        )

        primary_path = None
        blocks: list[Any] = []
        args = arguments or {}

        if isinstance(metadata, dict) and isinstance(metadata.get("path"), str):
            primary_path = display_path_relative_to_cwd(metadata["path"], self.cwd)

        if not success:
            # Error case - show error message
            error_msg = error or output or "Unknown error"
            blocks.append(Text(error_msg, style="error"))
        elif name == "read_file":
            if primary_path:
                extracted = self._extract_read_file_code(output)

                if extracted:
                    _start_line, code = extracted
                else:
                    _start_line, code = 1, output

                shown_start = metadata.get("shown_start_line") if metadata else None
                shown_end = metadata.get("shown_end_line") if metadata else None
                total_lines = metadata.get("total_lines") if metadata else None

                lexer = self._guess_language(primary_path)

                header_parts = [display_path_relative_to_cwd(primary_path, self.cwd)]
                header_parts.append(" ✦ ")

                if shown_start and shown_end and total_lines:
                    header_parts.append(f"Lines {shown_start}-{shown_end} of {total_lines}")

                header = "".join(header_parts)
                blocks.append(Text(header, style="tool.arg"))

                blocks.append(
                    Syntax(
                        code,
                        lexer,
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=False,
                        padding=(1, 2),
                    )
                )
            else:
                output_display = truncate_text_to_token_limit(
                    output,
                    max_tokens=self.tool_output_display_tokens,
                    model=self._model,
                )
                blocks.append(
                    Syntax(
                        output_display,
                        "text",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=False,
                        padding=(1, 2),
                    )
                )

        elif tool_kind in ("write", "edit") and success and diff:
            output_line = output.strip() if output else "Completed"
            blocks.append(Text(output_line, style="tool.result"))
            diff_text = diff.create_diff_summary() if isinstance(diff, FileDiff) else str(diff)
            if diff_text:
                diff_display = truncate_text_to_token_limit(
                    diff_text,
                    max_tokens=self.tool_output_display_tokens,
                    model=self._model,
                )
                blocks.append(
                    Syntax(
                        diff_display,
                        "diff",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=True,
                        padding=(1, 2),
                    )
                )

        elif name == "batch_apply":
            # Batch apply: show summary and combined diff
            output_line = output.strip() if output else "Batch completed"
            blocks.append(Text(output_line, style="tool.result" if success else "error"))

            if diff:
                diff_str = diff.create_diff_summary() if isinstance(diff, FileDiff) else str(diff)
                diff_display = truncate_text_to_token_limit(
                    diff_str,
                    max_tokens=self.tool_output_display_tokens,
                    model=self._model,
                )
                blocks.append(
                    Syntax(
                        diff_display,
                        "diff",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=True,
                        padding=(1, 2),
                    )
                )
        elif tool_kind == "shell":
            command = args.get("command") if args else None
            if isinstance(command, str):
                blocks.append(Text(f"$ {command.strip()}", style="command"))
            if exit_code is not None:
                blocks.append(Text(f"(Exit Code: {exit_code})", style="tool.arg"))
            output_display = truncate_text_to_token_limit(
                output,
                max_tokens=self.tool_output_display_tokens,
                model=self._model,
            )
            if output_display.count("\n") <= 1 and len(output_display) <= 240:
                blocks.append(Text(output_display.rstrip("\n"), style="dim"))
            else:
                blocks.append(
                    Syntax(
                        output_display,
                        "text",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        word_wrap=True,
                    )
                )
        elif name == "list_dir":
            total_entries = metadata.get("total_entries") if metadata else None
            shown_entries = metadata.get("shown_entries") if metadata else None
            path = metadata.get("path") if metadata else None
            depth = metadata.get("depth") if metadata else None

            if path is not None:
                header_parts = [display_path_relative_to_cwd(path, self.cwd)]
                if total_entries is not None and shown_entries is not None:
                    header_parts.append(f" • {shown_entries}/{total_entries} entries")
                if depth is not None:
                    header_parts.append(f" • depth={depth}")
                blocks.append(Text("".join(header_parts), style="tool.arg"))

            # Show the tree output
            if output:
                blocks.append(
                    Syntax(
                        output,
                        "text",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=False,
                        padding=(1, 2),
                    )
                )
        elif name == "grep":
            total_matches = metadata.get("total_matches", 0) if metadata else 0
            files_matched = metadata.get("files_matched", 0) if metadata else 0
            pattern = metadata.get("pattern", "") if metadata else ""
            is_truncated = metadata.get("truncated", False) if metadata else False

            header = f'"{pattern}" → {total_matches} matches in {files_matched} files'
            if is_truncated:
                header += " (truncated)"
            blocks.append(Text(header, style="tool.arg"))

            if output:
                blocks.append(
                    Syntax(
                        output,
                        "text",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=False,
                        padding=(1, 2),
                    )
                )
        elif name == "glob":
            total_matches = metadata.get("total_matches", 0) if metadata else 0
            shown_matches = metadata.get("shown_matches", 0) if metadata else 0
            pattern = metadata.get("pattern", "") if metadata else ""
            is_truncated = metadata.get("truncated", False) if metadata else False

            header = f'"{pattern}" → {shown_matches}/{total_matches} matches'
            if is_truncated:
                header += " (truncated)"
            blocks.append(Text(header, style="tool.arg"))

            if output:
                blocks.append(
                    Syntax(
                        output,
                        "text",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=False,
                        padding=(1, 2),
                    )
                )
        elif name == "web_search":
            query = metadata.get("query", "") if metadata else ""
            backend = metadata.get("backend", "") if metadata else ""
            result_count = metadata.get("result_count", 0) if metadata else 0
            is_cached = metadata.get("cached", False) if metadata else False
            cache_mode = metadata.get("mode", "") if metadata else ""

            # Build header
            cache_icon = "💾" if is_cached else "🔍"
            cache_text = " [cached]" if is_cached else ""
            mode_text = f" ({cache_mode})" if cache_mode else ""

            header = f'{cache_icon} "{query}" → {result_count} results (via {backend}){cache_text}{mode_text}'
            blocks.append(Text(header, style="tool.arg"))

            if output:
                output_display = truncate_text_to_token_limit(
                    output,
                    max_tokens=self.tool_output_display_tokens,
                    model=self._model,
                )
                blocks.append(
                    Syntax(
                        output_display,
                        "markdown",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=True,
                        padding=(1, 2),
                    )
                )
        elif name == "web_fetch":
            url = metadata.get("url", "") if metadata else ""
            content_length = metadata.get("content_length", 0) if metadata else 0
            is_cached = metadata.get("cached", False) if metadata else False
            cache_mode = metadata.get("mode", "") if metadata else ""
            pattern = metadata.get("pattern", "") if metadata else ""
            match_count = metadata.get("match_count", 0) if metadata else 0
            is_find = metadata.get("found") is not None if metadata else False

            # Build header based on action type
            cache_icon = "💾" if is_cached else "🌐"
            cache_text = " [cached]" if is_cached else ""
            mode_text = f" ({cache_mode})" if cache_mode else ""

            if is_find:
                # Find action
                found = metadata.get("found", False) if metadata else False
                if found:
                    header = f'🔎 Found {match_count} matches for "{pattern}" in {url}{cache_text}'
                else:
                    header = f'🔎 Pattern "{pattern}" not found in {url}{cache_text}'
            else:
                # Fetch action
                header = f"{cache_icon} Fetched: {url} ({content_length:,} chars){cache_text}{mode_text}"

            blocks.append(Text(header, style="tool.arg"))

            if output:
                output_display = truncate_text_to_token_limit(
                    output,
                    max_tokens=self.tool_output_display_tokens,
                    model=self._model,
                )
                blocks.append(
                    Syntax(
                        output_display,
                        "markdown",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=True,
                        padding=(1, 2),
                    )
                )
        else:
            # Generic success case for other tools
            output_display = truncate_text_to_token_limit(
                output,
                max_tokens=self.tool_output_preview_tokens,
                model=self._model,
            )
            blocks.append(Text(output_display, style="dim"))

        if truncated:
            blocks.append(
                Text(
                    "...[output truncated]",
                    style="warning",
                )
            )

        subtitle_text = "Failed" if not success else "Completed"
        panel = Panel(
            Group(*blocks),
            title=title,
            title_align="left",
            subtitle_align="right",
            subtitle=Text(subtitle_text, style=status_style),
            padding=(1, 2),
            box=box.ROUNDED,
            border_style=border_style,
        )
        return CommandDisplayPayload(renderables=[panel])

    def tool_call_complete(
        self,
        call_id: str,
        name: str,
        tool_kind: str | None,
        output: str,
        success: bool,
        diff: FileDiff | None,
        error: str | None,
        metadata: dict[str, Any] | None = None,
        truncated: bool = False,
        exit_code: int | None = None,
    ) -> None:
        payload = self.prepare_tool_call_complete_payload(
            call_id=call_id,
            name=name,
            tool_kind=tool_kind,
            output=output,
            success=success,
            diff=diff,
            error=error,
            metadata=metadata,
            truncated=truncated,
            exit_code=exit_code,
            arguments=self.get_tool_call_arguments_snapshot(call_id),
        )
        self.render_command_payload(payload.renderables)
        self.clear_tool_call_arguments(call_id)

    def print_welcome(
        self,
        banner: str,
        info: dict[str, str],
        tips: str,
    ) -> None:
        from rich.table import Table

        # Build info table
        info_table = Table.grid(padding=(0, 2))
        info_table.add_column(style="welcome.label", justify="right")
        info_table.add_column(style="welcome.value")
        for label, value in info.items():
            info_table.add_row(f"{label}", value)

        content = Group(
            Text(banner, style="banner"),
            Text(),
            info_table,
            Text(),
            Text(tips, style="welcome.tips"),
        )

        panel = Panel(
            content,
            padding=(1, 4),
            box=box.ROUNDED,
            border_style="border.focus",
        )
        self.console.print()
        self.console.print(panel)

    def prompt_workspace_trust(self, workspace: Path) -> bool:
        """Prompt user to confirm the workspace is trusted."""
        self.console.print()
        self.console.print(Rule(style="border"))
        self.console.print("\n[bold] Accessing workspace:[/bold]\n")
        self.console.print(f" [path]{workspace.resolve()}[/path]\n")
        self.console.print(
            " Quick safety check: Is this a project you created or one you trust? "
            "If not, review files in this folder first.\n"
        )
        self.console.print(" pichu will be able to read, edit, and execute files here.\n")
        self.console.print(" [dim]Security guide: review unfamiliar files before allowing access.[/dim]\n")

        options = [
            (True, "Yes, I trust this folder"),
            (False, "No, exit"),
        ]
        trusted = self._interactive_select(options, default_index=0)
        self.console.print()
        return bool(trusted)

    def print_goodbye(self, message: str) -> None:
        self._renderer.close()
        self.console.print()
        self.console.print(Rule(style="border.muted"))
        self.console.print(f"[goodbye]  \u2726 {message}[/goodbye]")
        self.console.print(Rule(style="border.muted"))
        self.console.print()

    def print_task_panel(
        self,
        tasks: list[dict],
        stats: dict[str, int],
    ) -> None:
        """Render a task summary panel with grouped status.

        Args:
            tasks: List of task dicts with id, subject, status, blocked_by, blocks, owner
            stats: Dict with total, completed, in_progress, available, blocked counts
        """
        from rich.tree import Tree

        if not tasks:
            self.console.print(
                Panel(
                    Text("No tasks. Use task_create to add tasks.", style="dim"),
                    title="[bold]📋 Tasks[/]",
                    border_style="border.muted",
                )
            )
            return

        tree = Tree(f"[bold]📋 Tasks ({stats['completed']}/{stats['total']} completed)[/]")

        # Group tasks by status
        in_progress = [t for t in tasks if t.get("status") == "in_progress"]
        available = [t for t in tasks if t.get("status") == "pending" and not t.get("blocked_by")]
        blocked = [t for t in tasks if t.get("status") == "pending" and t.get("blocked_by")]
        completed = [t for t in tasks if t.get("status") == "completed"]

        # In Progress
        if in_progress:
            branch = tree.add("[task.in_progress]▶ In Progress[/task.in_progress]")
            for task in in_progress:
                owner_str = f" (owner: {task['owner']})" if task.get("owner") else ""
                branch.add(f"[task.in_progress]{task['id']}: {task['subject']}{owner_str}[/task.in_progress]")

        # Available
        if available:
            branch = tree.add("[task.available]◼ Available[/task.available]")
            for task in available:
                branch.add(f"[task.available]{task['id']}: {task['subject']}[/task.available]")

        # Blocked
        if blocked:
            branch = tree.add("[task.blocked]⚠ Blocked[/task.blocked]")
            for task in blocked:
                blockers = ", ".join(task.get("blocked_by", [])[:3])
                if len(task.get("blocked_by", [])) > 3:
                    blockers += f" +{len(task['blocked_by']) - 3}"
                branch.add(f"[task.blocked]{task['id']}: {task['subject']}[/task.blocked]")
                branch.add(f"[error]   ← waiting on: {blockers}[/error]")

        # Completed (last 5)
        if completed:
            branch = tree.add(f"[bold dim]✔ Completed ({len(completed)} tasks)[/]")
            for task in completed[-5:]:
                branch.add(f"[dim]{task['id']}: {task['subject']}[/]")
            if len(completed) > 5:
                branch.add(f"[dim]... and {len(completed) - 5} more[/]")

        panel = Panel(
            tree,
            border_style="border.focus",
            subtitle=Text(
                f"{stats['in_progress']} active, {stats['available']} ready, {stats['blocked']} blocked",
                style="dim",
            ),
            subtitle_align="right",
        )
        self.console.print()
        self.console.print(panel)

    def print_task_event(
        self,
        event_type: str,
        task_id: str,
        subject: str,
        details: str | None = None,
    ) -> None:
        """Print a task lifecycle event inline.

        Args:
            event_type: One of "created", "started", "completed", "updated", "deleted"
            task_id: Task ID
            subject: Task subject
            details: Optional additional info (e.g., unblocked tasks)
        """
        icons = {
            "created": ("✔", "success"),
            "started": ("▶", "warning"),
            "completed": ("✔", "success"),
            "updated": ("↻", "info"),
            "deleted": ("✖", "error"),
        }

        icon, style = icons.get(event_type, ("•", "dim"))

        text = Text()
        text.append(f"  {icon} ", style=style)
        text.append(f"Task {event_type}: ", style=style)
        text.append(f"{task_id} ", style="dim")
        text.append(subject, style="bold")

        if details:
            text.append(f" ({details})", style="dim")

        self.console.print(text)

    def print_context_event(
        self,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        """Print a context management event (compaction/pruning).

        Args:
            event_type: One of "compacted" or "pruned"
            details: Event-specific details
        """
        text = Text()

        if event_type == "compacted":
            original = details.get("original_tokens", 0)
            new = details.get("new_tokens", 0)
            saved = details.get("tokens_saved", 0)
            text.append("  ⚡ ", style="warning")
            text.append("Context compacted: ", style="warning")
            text.append(f"{original:,} → {new:,} tokens ", style="dim")
            text.append(f"(saved {saved:,})", style="success")
        elif event_type == "pruned":
            count = details.get("pruned_count", 0)
            saved = details.get("tokens_saved", 0)
            text.append("  🗑️ ", style="info")
            text.append("Tool outputs pruned: ", style="info")
            text.append(f"{count} outputs cleared ", style="dim")
            text.append(f"(~{saved:,} tokens)", style="success")

        self.console.print(text)

    def print_context_stats(self, stats: dict[str, Any]) -> None:
        """Print context window usage stats in debug mode."""
        self.update_context_tracker(stats)
        total = stats.get("total_tokens", 0)
        limit = stats.get("context_limit", 0)
        pct = stats.get("percentage", 0)

        # Format numbers for display
        def fmt(n: int) -> str:
            if n >= 1000:
                return f"{n / 1000:.1f}K"
            return str(n)

        # Calculate sub-percentages
        def sub_pct(n: int) -> float:
            return round(n / limit * 100, 1) if limit > 0 else 0

        sys_tokens = stats.get("system_tokens", 0)
        tool_def = stats.get("tool_def_tokens", 0)
        user = stats.get("user_tokens", 0)
        assistant = stats.get("assistant_tokens", 0)
        tool_result = stats.get("tool_result_tokens", 0)

        # Build the display
        self.console.print()
        self.console.print("  ┌─ Context Window ──────────────────────────┐", style="border")
        self.console.print(f"  │  {fmt(total)} / {fmt(limit)} tokens", style="dim", end="")

        # Color based on percentage
        if pct >= 80:
            pct_style = "error"
        elif pct >= 60:
            pct_style = "warning"
        else:
            pct_style = "success"
        self.console.print("  • ", style="dim", end="")
        self.console.print(f"{pct}%", style=pct_style)

        self.console.print("  │", style="border")
        self.console.print(f"  │  System         {fmt(sys_tokens):>6}", style="border", end="")
        self.console.print(f"  ({sub_pct(sys_tokens):.1f}%)", style="tokens.input")
        self.console.print(f"  │  Tool Defs      {fmt(tool_def):>6}", style="border", end="")
        self.console.print(f"  ({sub_pct(tool_def):.1f}%)", style="tokens.input")
        self.console.print(f"  │  User Messages  {fmt(user):>6}", style="border", end="")
        self.console.print(f"  ({sub_pct(user):.1f}%)", style="tokens.output")
        self.console.print(f"  │  Assistant      {fmt(assistant):>6}", style="border", end="")
        self.console.print(f"  ({sub_pct(assistant):.1f}%)", style="tokens.output")
        self.console.print(f"  │  Tool Results   {fmt(tool_result):>6}", style="border", end="")
        self.console.print(f"  ({sub_pct(tool_result):.1f}%)", style="tokens.cached")
        self.console.print("  └────────────────────────────────────────────┘", style="border")

    def prompt_tool_approval(self, confirmation: "ToolConfirmation") -> "ApprovalResponse":
        """Display a rich-formatted approval prompt with interactive selector.

        Shows tool name, kind, parameters, diff (if any), and danger warnings.
        User navigates with ↑/↓ arrow keys and confirms with Enter.
        Returns an ApprovalResponse enum value.
        """
        from safety.approval import ApprovalResponse

        kind_icons = {
            "shell": "⚡",
            "write": "✏️",
            "network": "🌐",
            "memory": "🧠",
            "mcp": "🔌",
        }
        kind_val = confirmation.tool_kind.value if confirmation.tool_kind else "unknown"
        icon = kind_icons.get(kind_val, "🔧")

        self.console.print()

        # Build approval panel content
        lines: list[str] = []

        # Description / summary
        lines.append(f"[bold]{confirmation.description}[/bold]")
        lines.append("")

        # Command (shell tools)
        if confirmation.command:
            lines.append(f"[dim]Command:[/dim]  [command]{confirmation.command}[/command]")

        # Affected paths
        if confirmation.affected_paths:
            paths_str = ", ".join(display_path_relative_to_cwd(Path(p), self.cwd) for p in confirmation.affected_paths)
            lines.append(f"[dim]Files:[/dim]    {paths_str}")

        # Danger warning
        if confirmation.is_dangerous:
            lines.append("")
            lines.append("[error]⚠  This operation is flagged as DANGEROUS[/error]")

        panel_content = "\n".join(lines)

        self.console.print(
            Panel(
                panel_content,
                title=f"{icon} [bold]{confirmation.tool_name}[/bold] requires approval",
                title_align="left",
                border_style="warning" if not confirmation.is_dangerous else "error",
                padding=(0, 1),
            )
        )

        # Show diff if present
        if confirmation.diff:
            diff_text = confirmation.diff.create_diff_summary()
            if diff_text.strip():
                self.console.print(
                    Syntax(
                        diff_text,
                        "diff",
                        theme=SYNTAX_HIGHLIGHT_THEME,
                        line_numbers=False,
                        word_wrap=True,
                    ),
                    style="dim",
                )

        # Interactive selector
        options = [
            (ApprovalResponse.YES, "Yes, allow this action"),
            (ApprovalResponse.ALWAYS, "Always allow (remember for session)"),
            (ApprovalResponse.NO, "No, deny this action"),
        ]

        result = self._interactive_select(options, default_index=0)

        if result in {ApprovalResponse.YES, ApprovalResponse.ALWAYS}:
            label = "Approved" if result == ApprovalResponse.YES else "Approved (remembered for session)"
            self.console.print(f"  [success]✓ {label}[/success]")
        else:
            self.console.print("  [error]✗ Denied[/error]")

        return result

    def _interactive_select(
        self,
        options: list[tuple["Any", str]],
        default_index: int = 0,
    ) -> "Any":
        """Render an arrow-key navigable selector and return the chosen value.

        Works cross-platform: uses msvcrt on Windows, tty/termios on Unix.
        Supports: ↑/↓ to move, Enter to confirm, Esc to cancel (returns last option).
        """
        import sys

        if not getattr(self, "_interactive_tty", False):
            return options[-1][0]

        selected = default_index

        def _render(sel: int) -> None:
            """Print the options list with the selected item highlighted."""
            for i, (_, label) in enumerate(options):
                if i == sel:
                    self.console.print(f"  [highlight] > {i + 1}. {label}[/highlight]")
                else:
                    self.console.print(f"  [dim]   {i + 1}. {label}[/dim]")
            self.console.print()
            self.console.print("  [dim]↑/↓ to select · Enter to confirm · Esc to cancel[/dim]")

        def _clear(count: int) -> None:
            """Move cursor up and clear lines to redraw the selector."""
            for _ in range(count):
                sys.stdout.write("\033[A\033[2K")
            sys.stdout.flush()

        render_lines = len(options) + 2  # options + blank line + hint line

        _render(selected)

        try:
            if sys.platform == "win32":
                import msvcrt

                while True:
                    key = msvcrt.getwch()
                    if key == "\r":  # Enter
                        return options[selected][0]
                    elif key == "\x1b":  # Esc
                        return options[-1][0]  # last option = deny
                    elif key in ("\x00", "\xe0"):  # special key prefix
                        arrow = msvcrt.getwch()
                        if arrow == "H":  # Up
                            selected = (selected - 1) % len(options)
                        elif arrow == "P":  # Down
                            selected = (selected + 1) % len(options)
                    elif key.isdigit() and key != "0":
                        idx = int(key) - 1
                        if 0 <= idx < len(options):
                            return options[idx][0]
                    else:
                        continue
                    _clear(render_lines)
                    _render(selected)
            else:
                import termios
                import tty

                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    while True:
                        ch = sys.stdin.read(1)
                        if ch == "\r" or ch == "\n":  # Enter
                            return options[selected][0]
                        elif ch == "\x1b":  # Escape sequence
                            seq1 = sys.stdin.read(1)
                            if seq1 == "[":
                                seq2 = sys.stdin.read(1)
                                if seq2 == "A":  # Up
                                    selected = (selected - 1) % len(options)
                                elif seq2 == "B":  # Down
                                    selected = (selected + 1) % len(options)
                            else:
                                # Plain Esc
                                return options[-1][0]
                        elif ch.isdigit() and ch != "0":
                            idx = int(ch) - 1
                            if 0 <= idx < len(options):
                                return options[idx][0]
                        else:
                            continue
                        _clear(render_lines)
                        _render(selected)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (KeyboardInterrupt, EOFError):
            return options[-1][0]  # deny on interrupt

    def print_approval_denied(self, tool_name: str, reason: str) -> None:
        """Print a notice that a tool was denied."""
        self.console.print(
            f"  [error]✗[/error] [dim]{tool_name}[/dim] denied: {reason}",
        )

    def display_hooks(self, hook_engine: Any) -> None:
        """Display all registered hooks in a rich table (via /hooks command)."""
        if hook_engine is None or not hook_engine.has_hooks:
            self.console.print()
            self.console.print(
                Panel(
                    "[dim]No hooks registered. Add hooks to "
                    "[path].pichu/config.toml[/path] under [code.inline]\\[hooks][/code.inline].[/dim]",
                    title="[info] Hooks[/info]",
                    border_style="border",
                    padding=(1, 2),
                )
            )
            return

        events = hook_engine.get_registered_events()
        total = hook_engine.get_hook_count()

        table = Table(
            title=f"Registered Hooks ({total} handler{'s' if total != 1 else ''})",
            title_style="info",
            box=box.ROUNDED,
            border_style="border",
            show_lines=True,
            padding=(0, 1),
        )
        table.add_column("Event", style="accent", min_width=18)
        table.add_column("Matcher", style="secondary", min_width=12)
        table.add_column("Type", style="dim", min_width=8)
        table.add_column("Command", style="code.inline", max_width=60, overflow="fold")
        table.add_column("Timeout", style="dim", justify="right", min_width=7)
        table.add_column("Async", style="dim", justify="center", min_width=5)

        for event in events:
            for registration in hook_engine.get_registrations(event):
                handler = registration.handler
                pattern = registration.matcher or "*"
                table.add_row(
                    event.value,
                    pattern,
                    handler.type,
                    handler.command[:80] + ("..." if len(handler.command) > 80 else ""),
                    f"{handler.timeout}s",
                    "✓" if handler.async_ else "",
                )

        self.console.print()
        self.console.print(table)

        # Status line
        status_parts = [
            f"[accent]{len(events)}[/accent] event{'s' if len(events) != 1 else ''}",
            f"[accent]{total}[/accent] handler{'s' if total != 1 else ''}",
        ]
        if hook_engine.stop_hook_active:
            status_parts.append("[warning]stop_hook_active[/warning]")
        self.console.print(
            f"  {' · '.join(status_parts)}",
            style="dim",
        )
        self.console.print()

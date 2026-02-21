"""Display and scaffold lifecycle hook configuration."""

from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence
from pathlib import Path
from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand
from commands.builtin._scaffold import ensure_core_project_scaffold, project_config_path, relative_to_project

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


_HOOK_SCRIPT_TEMPLATES: dict[str, str] = {
    "log_tool_use.py": """#!/usr/bin/env python3
\"\"\"PostToolUse hook: Log all successful tool executions.\"\"\"

import json
import os
import sys
from datetime import datetime


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})
    session_id = input_data.get("session_id", "unknown")
    cwd = input_data.get("cwd", "")

    timestamp = datetime.now().isoformat()
    if tool_name == "shell":
        detail = tool_input.get("command", "")
    elif "path" in tool_input:
        detail = tool_input["path"]
    elif "file_path" in tool_input:
        detail = tool_input["file_path"]
    else:
        detail = json.dumps(tool_input)[:100]

    log_line = f"[{timestamp}] session={session_id[:8]} tool={tool_name} detail={detail}\\n"
    log_dir = os.path.join(cwd, "logs", "hooks")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "tool_use.log"), "a", encoding="utf-8") as handle:
        handle.write(log_line)

    sys.exit(0)


if __name__ == "__main__":
    main()
""",
    "auto_format.py": """#!/usr/bin/env python3
\"\"\"PostToolUse hook: Run black on edited Python files.\"\"\"

import json
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if file_path.endswith(".py"):
    subprocess.run(["black", "--quiet", file_path], check=False)

sys.exit(0)
""",
    "block_dangerous.py": """#!/usr/bin/env python3
\"\"\"PreToolUse hook: Block dangerous shell commands.\"\"\"

import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"rm\\s+(-rf?|--recursive)\\s+[/~]",
    r"rm\\s+-rf?\\s+\\*",
    r"dd\\s+if=",
    r"mkfs",
    r"shutdown",
    r"reboot",
    r"chmod\\s+(-R\\s+)?777\\s+[/~]",
    r"curl\\s+.*\\|\\s*(bash|sh)",
    r"wget\\s+.*\\|\\s*(bash|sh)",
]


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    command = input_data.get("tool_input", {}).get("command", "")
    if tool_name != "shell" or not command:
        sys.exit(0)

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Blocked dangerous command matching pattern: {pattern}",
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
""",
    "protect_files.py": """#!/usr/bin/env python3
\"\"\"PreToolUse hook: Protect sensitive files from edits.\"\"\"

import json
import sys

PROTECTED_PATTERNS = [
    ".env",
    ".env.",
    ".git/",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".key",
    "secrets",
]


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("path", "") or tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    normalized = file_path.replace("\\\\", "/").lower()
    for pattern in PROTECTED_PATTERNS:
        if pattern.lower() in normalized:
            print(f"Protected file: {file_path} matches pattern '{pattern}'", file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
""",
}

_DEFAULT_HOOK_HANDLERS: tuple[tuple[str, str | None, str, int | None], ...] = (
    ("PostToolUse", None, "python .pichu/hooks/log_tool_use.py", None),
    ("PostToolUse", "write_file|edit_file", "python .pichu/hooks/auto_format.py", None),
    ("PreToolUse", "shell", "python .pichu/hooks/block_dangerous.py", 10),
    ("PreToolUse", "write_file|edit_file", "python .pichu/hooks/protect_files.py", 5),
)


class HooksCommand(SlashCommand):
    name = "hooks"
    description = "Display or initialize lifecycle hooks"
    usage = "/hooks [init]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        action = args.strip().lower() if args.strip() else "list"
        if action == "init":
            return await self._init_hooks(tui, config)

        if not session:
            return CommandResult(error="No active session.")

        return CommandResult(
            display=CommandDisplayPayload(renderables=self._build_hook_renderables(session.hook_engine))
        )

    async def _init_hooks(self, tui: "TUI", config: "Config") -> CommandResult:
        created_core = ensure_core_project_scaffold(config.cwd)
        hooks_dir = project_config_path(config.cwd).parent / "hooks"
        hooks_dir_existed = hooks_dir.exists()
        hooks_dir.mkdir(parents=True, exist_ok=True)

        created: list[str] = []
        created.extend(created_core)
        if not hooks_dir_existed:
            created.append(f"{relative_to_project(hooks_dir, config.cwd)}/")

        for file_name, template in _HOOK_SCRIPT_TEMPLATES.items():
            path = hooks_dir / file_name
            if path.exists():
                continue
            path.write_text(template, encoding="utf-8")
            created.append(relative_to_project(path, config.cwd))

        config_file = project_config_path(config.cwd)
        try:
            added_handlers = self._ensure_default_hook_config(config_file)
        except ValueError as exc:
            return CommandResult(error=str(exc))

        lines: list[str] = [""]
        if created:
            for item in created:
                lines.append(f"  [success]✓[/success] Created {item}")
        else:
            lines.append("  [dim]Hook files already initialized.[/dim]")

        if added_handlers:
            lines.append(f"  [success]✓[/success] Added {added_handlers} hook handler(s) to config")
        else:
            lines.append("  [dim]Hook config already contains default handlers.[/dim]")

        lines.append("  [dim]Hook config changes apply on the next session start.[/dim]")
        lines.append("")
        return CommandResult(display=CommandDisplayPayload(renderables=lines))

    @staticmethod
    def _build_hook_renderables(hook_engine) -> list[object]:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        if hook_engine is None or not hook_engine.has_hooks:
            return [
                "",
                Panel(
                    "[dim]No hooks registered. Add hooks to "
                    "[path].pichu/config.toml[/path] under [code.inline]\\[hooks][/code.inline].[/dim]",
                    title="[info] Hooks[/info]",
                    border_style="border",
                    padding=(1, 2),
                ),
            ]

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

        status_parts = [
            f"[accent]{len(events)}[/accent] event{'s' if len(events) != 1 else ''}",
            f"[accent]{total}[/accent] handler{'s' if total != 1 else ''}",
        ]
        if hook_engine.stop_hook_active:
            status_parts.append("[warning]stop_hook_active[/warning]")

        return [
            "",
            table,
            f"  {' · '.join(status_parts)}",
            "",
        ]

    def _ensure_default_hook_config(self, config_path: Path) -> int:
        from tomlkit import aot, document, dumps, parse, table

        if config_path.exists():
            doc = parse(config_path.read_text(encoding="utf-8"))
        else:
            doc = document()

        hooks = doc.get("hooks")
        if hooks is None:
            hooks = table()
            doc["hooks"] = hooks
        elif not isinstance(hooks, MutableMapping):
            raise ValueError(f"Invalid config format: [hooks] must be a table in {config_path}.")

        added_handlers = 0
        for event_name, matcher, command, timeout in _DEFAULT_HOOK_HANDLERS:
            event_matchers = hooks.get(event_name)
            if event_matchers is None:
                event_matchers = aot()
                hooks[event_name] = event_matchers
            elif not isinstance(event_matchers, MutableSequence):
                raise ValueError(f"Invalid config format: [hooks.{event_name}] must be an array of tables.")

            if self._has_handler(event_matchers, matcher, command):
                continue

            matcher_table = table()
            if matcher is not None:
                matcher_table["matcher"] = matcher

            handler_table = table()
            handler_table["type"] = "command"
            handler_table["command"] = command
            if timeout is not None:
                handler_table["timeout"] = timeout

            handlers = aot()
            handlers.append(handler_table)
            matcher_table["hooks"] = handlers
            event_matchers.append(matcher_table)
            added_handlers += 1

        if added_handlers:
            config_path.write_text(dumps(doc), encoding="utf-8")

        return added_handlers

    @staticmethod
    def _has_handler(event_matchers: MutableSequence, matcher: str | None, command: str) -> bool:
        for matcher_entry in event_matchers:
            if not isinstance(matcher_entry, MutableMapping):
                continue
            existing_matcher = matcher_entry.get("matcher")
            normalized_matcher = str(existing_matcher) if existing_matcher is not None else None
            if normalized_matcher != matcher:
                continue

            handlers = matcher_entry.get("hooks")
            if not isinstance(handlers, MutableSequence):
                continue
            for handler in handlers:
                if not isinstance(handler, MutableMapping):
                    continue
                existing_command = str(handler.get("command", "")).strip()
                if existing_command == command:
                    return True
        return False

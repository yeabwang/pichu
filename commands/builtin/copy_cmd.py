"""Copy last assistant response to clipboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


def _copy_to_clipboard(text: str) -> bool:
    """Cross-platform clipboard copy."""
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            process = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
            process.communicate(text.encode("utf-16le"))
            return process.returncode == 0
        elif sys.platform == "darwin":
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            return process.returncode == 0
        else:
            # Linux — try xclip, then xsel
            for cmd in [
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ]:
                try:
                    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    process.communicate(text.encode("utf-8"))
                    if process.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
            return False
    except Exception:
        return False


class CopyCommand(SlashCommand):
    name = "copy"
    description = "Copy last response to clipboard"
    usage = "/copy"
    aliases = ["cp"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session or not session.context_manager:
            return CommandResult(error="No active session.")

        cm = session.context_manager

        # Find the last assistant message
        last_assistant = None
        for msg in reversed(cm._messages):
            if msg.role == "assistant" and msg.content:
                last_assistant = msg.content
                break

        if not last_assistant:
            return CommandResult(error="No assistant response to copy.")

        success = _copy_to_clipboard(last_assistant)

        if success:
            preview = last_assistant[:80].replace("\n", " ")
            if len(last_assistant) > 80:
                preview += "..."
            tui.console.print()
            tui.console.print(f"  [success]✓[/success] Copied to clipboard ({len(last_assistant)} chars)")
            tui.console.print(f"  [dim]{preview}[/dim]")
            tui.console.print()
        else:
            return CommandResult(error="Failed to copy to clipboard. Clipboard utility not found.")

        return CommandResult()

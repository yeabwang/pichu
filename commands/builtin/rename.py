"""Slash command: /rename — rename the current session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class RenameCommand(SlashCommand):
    name = "rename"
    description = "Rename the current session"
    usage = "/rename <title>"
    aliases = ["title"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session.storage:
            return CommandResult(error="Session persistence is not enabled. Check your config.")

        title = args.strip()
        if not title:
            current = session.get_title()
            if current:
                tui.console.print(f"[dim]Current title:[/dim] {current}")
            return CommandResult(error="Usage: /rename <title>")

        session.set_title(title)
        tui.console.print(f"[green]✓[/green] Session renamed to: [bold]{title}[/bold]")
        return CommandResult()

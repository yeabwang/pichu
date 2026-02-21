"""Slash command: /fork — create a branch of the current session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ForkCommand(SlashCommand):
    name = "fork"
    description = "Fork this session into a new branch"
    usage = "/fork [title]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session.storage:
            return CommandResult(error="Session persistence is not enabled. Check your config.")

        title = args.strip() if args.strip() else ""

        forked = session.fork(new_title=title)
        if not forked:
            return CommandResult(error="Failed to fork session.")

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    "[green]✓[/green] Session forked!",
                    f"  [dim]New session ID:[/dim] [cyan]{forked.session_id[:8]}[/cyan]",
                    f"  [dim]Title:[/dim] {forked.get_title()}",
                    f"  [dim]Resume with:[/dim] [bold]--resume {forked.session_id[:8]}[/bold]",
                ]
            )
        )

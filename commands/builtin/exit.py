"""Exit the interactive session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ExitCommand(SlashCommand):
    name = "exit"
    description = "Exit the session"
    usage = "/exit"
    aliases = ["quit", "q"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        return CommandResult(should_exit=True)

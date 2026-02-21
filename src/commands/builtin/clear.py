"""Clear conversation history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ClearCommand(SlashCommand):
    name = "clear"
    description = "Clear conversation history"
    usage = "/clear"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if session and session.context_manager:
            session.context_manager.clear()
            return CommandResult(should_clear=True)
        else:
            return CommandResult(error="No active session to clear.")

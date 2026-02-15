"""Display registered hooks and their configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class HooksCommand(SlashCommand):
    name = "hooks"
    description = "Display registered lifecycle hooks"
    usage = "/hooks"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session:
            return CommandResult(error="No active session.")

        # Delegate to the existing TUI display_hooks method
        tui.display_hooks(session.hook_engine)
        return CommandResult()

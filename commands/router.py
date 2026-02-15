"""Command router — parses user input and dispatches to slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from commands.base import CommandRegistry, CommandResult

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

logger = logging.getLogger(__name__)


class CommandRouter:
    """Parses slash-command input and dispatches to the right handler."""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def is_command(self, user_input: str) -> bool:
        """Check if input is a slash command."""
        stripped = user_input.strip()
        return stripped.startswith("/") and len(stripped) > 1

    def parse(self, user_input: str) -> tuple[str, str]:
        """Split input into (command_name, args).

        Examples:
            "/compact focus on auth" -> ("compact", "focus on auth")
            "/exit"                  -> ("exit", "")
            "/model gpt-4o"          -> ("model", "gpt-4o")
        """
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return ("", stripped)

        # Remove the leading /
        body = stripped[1:]

        # Split on first space
        parts = body.split(None, 1)
        name = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        return (name, args)

    async def dispatch(
        self,
        user_input: str,
        session: "Session",
        tui: "TUI",
        config: "Config",
    ) -> CommandResult:
        """Parse and dispatch a slash command.

        Returns:
            CommandResult from the executed command, or an error result
            if the command is not found.
        """
        name, args = self.parse(user_input)
        if not name:
            return CommandResult(error="Invalid command. Use /help to list commands.")
        command = self._registry.get(name)

        if command is None:
            suggestions = self._registry.suggest(name)
            hint = ""
            if suggestions:
                hint = f" Did you mean: {', '.join('/' + s for s in suggestions[:3])}?"
            return CommandResult(error=f"Unknown command: /{name}.{hint} Type /help for available commands.")

        try:
            return await command.execute(args, session, tui, config)
        except Exception as e:
            logger.exception(f"Error executing /{name}")
            return CommandResult(error=f"Command /{name} failed: {e}")

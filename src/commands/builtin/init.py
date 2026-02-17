"""Initialize project scaffold by orchestrating modular subsystem init commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand
from commands.builtin._scaffold import ensure_core_project_scaffold

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

_MODULAR_INIT_COMMANDS: tuple[str, ...] = ("agent", "memory", "cache", "hooks")


class InitCommand(SlashCommand):
    name = "init"
    description = "Scaffold project and run modular subsystem initializers"
    usage = "/init"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        created = ensure_core_project_scaffold(config.cwd)

        tui.console.print()
        if created:
            for item in created:
                tui.console.print(f"  [success]✓[/success] Created {item}")
        else:
            tui.console.print("  [dim]Core scaffold already initialized.[/dim]")

        tui.console.print("  [dim]Running modular subsystem initializers...[/dim]")
        for command_name in _MODULAR_INIT_COMMANDS:
            subcommand = self._resolve_subcommand(command_name)
            if subcommand is None:
                return CommandResult(error=f"Required initializer command '/{command_name} init' is not registered.")

            result = await subcommand.execute("init", session, tui, config)
            if result.error:
                return CommandResult(error=f"/{command_name} init failed: {result.error}")

        tui.console.print("  [success]✓[/success] Project scaffolding complete.")
        tui.console.print("  [dim]Run /login to configure provider and model.[/dim]")
        tui.console.print()
        return CommandResult()

    def _resolve_subcommand(self, name: str) -> SlashCommand | None:
        registry = getattr(self, "_registry", None)
        if registry is not None:
            command = registry.get(name)
            if command is not None:
                return command

        if name == "agent":
            from commands.builtin.agents import AgentsCommand

            return AgentsCommand()
        if name == "memory":
            from commands.builtin.memory import MemoryCommand

            return MemoryCommand()
        if name == "cache":
            from commands.builtin.cache import CacheCommand

            return CacheCommand()
        if name == "hooks":
            from commands.builtin.hooks import HooksCommand

            return HooksCommand()
        return None

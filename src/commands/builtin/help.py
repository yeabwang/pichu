"""Display help information and list available commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class HelpCommand(SlashCommand):
    name = "help"
    description = "Display help information and available commands"
    usage = "/help [command]"
    aliases = ["h", "?"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.table import Table

        registry = getattr(self, "_registry", None)

        if args.strip():
            requested = args.strip().lstrip("/").lower()
            command = registry.get(requested) if registry else None
            if not command:
                return CommandResult(error=f"Unknown command: /{requested}. Use /help to list commands.")
            spec = command.spec()
            aliases = ", ".join(f"/{alias}" for alias in spec.aliases) or "none"
            return CommandResult(
                output=(
                    f"/{spec.name}\n"
                    f"  Description: {spec.description or 'No description provided.'}\n"
                    f"  Usage: {spec.usage}\n"
                    f"  Aliases: {aliases}"
                )
            )

        table = Table(
            title="Available Commands",
            title_style="info",
            box=box.SIMPLE_HEAVY,
            border_style="border",
            padding=(0, 2),
            show_edge=False,
        )
        table.add_column("Command", style="accent", min_width=28, no_wrap=True)
        table.add_column("Aliases", style="dim", min_width=16, no_wrap=True)
        table.add_column("Description", style="dim")

        if registry:
            for spec in sorted(registry.specs(), key=lambda item: item.name):
                aliases = ", ".join(f"/{alias}" for alias in spec.aliases)
                table.add_row(
                    spec.usage or f"/{spec.name}",
                    aliases or "",
                    spec.description or "No description provided.",
                )
        else:
            table.add_row("/help", "", "Display this help information")

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    "",
                    table,
                    "",
                    "  [dim]Type /command to execute. Arguments shown in [brackets] are optional.[/dim]",
                    "",
                ]
            )
        )

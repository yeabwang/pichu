"""Change color theme."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand
from ui.theme import THEME_PRESETS

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ThemeCommand(SlashCommand):
    name = "theme"
    description = "Change color theme"
    usage = "/theme [name]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.table import Table
        from rich.theme import Theme

        requested = args.strip().lower()

        if not requested:
            # List available themes
            table = Table(
                title="Available Themes",
                box=box.SIMPLE,
                padding=(0, 2),
            )
            table.add_column("Name", style="accent", min_width=14)
            table.add_column("Description", style="dim")
            table.add_column("Preview", min_width=20)

            for name, theme in THEME_PRESETS.items():
                desc = theme.get("description", "")
                primary = theme.get("primary", "white")
                preview_style = primary.replace("bold ", "")
                table.add_row(name, desc, f"[{preview_style}]████ Sample ████[/{preview_style}]")

            return CommandResult(
                display=CommandDisplayPayload(
                    renderables=[
                        "",
                        table,
                        "",
                        "  [dim]Use /theme <name> to apply.[/dim]",
                        "",
                    ]
                )
            )

        if requested not in THEME_PRESETS:
            available = ", ".join(THEME_PRESETS.keys())
            return CommandResult(error=f"Unknown theme: {requested}. Available: {available}")

        theme_data = THEME_PRESETS[requested]

        # Apply theme overrides to the console
        # We filter out non-style keys
        style_overrides = {k: v for k, v in theme_data.items() if k != "description"}

        new_theme = Theme(style_overrides)
        tui.console.push_theme(new_theme)

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    "",
                    (
                        f"  [success]✓[/success] Theme changed to "
                        f"[{theme_data.get('primary', 'accent')}]{requested}[/{theme_data.get('primary', 'accent')}]"
                    ),
                    f"  [dim]{theme_data.get('description', '')}[/dim]",
                    "",
                ]
            )
        )

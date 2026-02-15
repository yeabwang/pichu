"""Change color theme."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

# Theme presets: name -> partial Rich theme overrides
THEME_PRESETS = {
    "cyber": {
        "description": "Neon cyber theme (default)",
        "primary": "bold cyan",
        "secondary": "magenta",
        "accent": "bright_green",
        "agent": "bold bright_cyan",
        "border.focus": "cyan",
        "banner": "bold bright_cyan",
        "prompt.symbol": "bold cyan",
    },
    "monokai": {
        "description": "Monokai-inspired warm tones",
        "primary": "bold bright_yellow",
        "secondary": "bright_magenta",
        "accent": "bright_green",
        "agent": "bold bright_yellow",
        "border.focus": "bright_yellow",
        "banner": "bold bright_yellow",
        "prompt.symbol": "bold bright_yellow",
    },
    "solarized": {
        "description": "Solarized colour palette",
        "primary": "bold blue",
        "secondary": "yellow",
        "accent": "green",
        "agent": "bold blue",
        "border.focus": "blue",
        "banner": "bold blue",
        "prompt.symbol": "bold blue",
    },
    "minimal": {
        "description": "Clean monochrome look",
        "primary": "bold white",
        "secondary": "dim white",
        "accent": "white",
        "agent": "bold white",
        "border.focus": "white",
        "banner": "bold white",
        "prompt.symbol": "bold white",
    },
    "forest": {
        "description": "Nature-inspired greens",
        "primary": "bold bright_green",
        "secondary": "green",
        "accent": "bright_yellow",
        "agent": "bold bright_green",
        "border.focus": "green",
        "banner": "bold bright_green",
        "prompt.symbol": "bold bright_green",
    },
    "ocean": {
        "description": "Deep blue tones",
        "primary": "bold bright_blue",
        "secondary": "cyan",
        "accent": "bright_cyan",
        "agent": "bold bright_blue",
        "border.focus": "bright_blue",
        "banner": "bold bright_blue",
        "prompt.symbol": "bold bright_blue",
    },
}


class ThemeCommand(SlashCommand):
    name = "theme"
    description = "Change color theme"
    usage = "/theme [name]"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.table import Table
        from rich.theme import Theme

        requested = args.strip().lower()

        if not requested:
            # List available themes
            tui.console.print()
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

            tui.console.print(table)
            tui.console.print()
            tui.console.print("  [dim]Use /theme <name> to apply.[/dim]")
            tui.console.print()
            return CommandResult()

        if requested not in THEME_PRESETS:
            available = ", ".join(THEME_PRESETS.keys())
            return CommandResult(error=f"Unknown theme: {requested}. Available: {available}")

        theme_data = THEME_PRESETS[requested]

        # Apply theme overrides to the console
        # We filter out non-style keys
        style_overrides = {k: v for k, v in theme_data.items() if k != "description"}

        new_theme = Theme(style_overrides)
        tui.console.push_theme(new_theme)

        tui.console.print()
        tui.console.print(
            f"  [success]✓[/success] Theme changed to [{theme_data.get('primary', 'accent')}]{requested}[/{theme_data.get('primary', 'accent')}]"
        )
        tui.console.print(f"  [dim]{theme_data.get('description', '')}[/dim]")
        tui.console.print()

        return CommandResult()

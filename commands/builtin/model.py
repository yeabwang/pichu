"""Switch or view the AI model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

# Known model presets (can be extended via config)
_MODEL_PRESETS = {
    "devstral": "mistralai/devstral-2512:free",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-4.1-nano": "gpt-4.1-nano",
    "gemini-pro": "google/gemini-2.5-pro-preview",
    "gemini-flash": "google/gemini-2.5-flash-preview-05-20",
    "deepseek-r1": "deepseek/deepseek-r1:free",
    "deepseek-v3": "deepseek/deepseek-chat-v3-0324:free",
    "qwen-coder": "qwen/qwen3-235b-a22b:free",
}


class ModelCommand(SlashCommand):
    name = "model"
    description = "Switch or view the AI model"
    usage = "/model [name]"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.table import Table

        requested = args.strip()

        if not requested:
            # Show current model and available presets
            tui.console.print()
            tui.console.print(f"  [dim]Current model:[/dim] [accent]{config.model}[/accent]")
            tui.console.print()

            table = Table(
                title="Available Presets",
                box=box.SIMPLE,
                show_header=True,
                header_style="bold",
                padding=(0, 2),
            )
            table.add_column("Shortcut", style="accent", min_width=16)
            table.add_column("Model ID", style="dim", min_width=40)

            for shortcut, model_id in sorted(_MODEL_PRESETS.items()):
                marker = " ◀" if model_id == config.model else ""
                table.add_row(shortcut, f"{model_id}{marker}")

            tui.console.print(table)
            tui.console.print()
            tui.console.print("  [dim]Use /model <name> to switch. Names or full model IDs accepted.[/dim]")
            tui.console.print()
            return CommandResult()

        # Resolve model name
        model_id = _MODEL_PRESETS.get(requested.lower(), requested)

        # Apply the change
        old_model = config.model
        config.llm.model = model_id

        # Reinitialize the LLM client with new model
        if session and session.client:
            session.client._config.model = model_id

        tui.console.print()
        tui.console.print(
            f"  [success]✓[/success] Model switched: [dim]{old_model}[/dim] → [accent]{model_id}[/accent]"
        )
        tui.console.print()

        return CommandResult()

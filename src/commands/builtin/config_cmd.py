"""Open or display settings configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ConfigCommand(SlashCommand):
    name = "config"
    description = "View or open settings configuration"
    usage = "/config"
    aliases = ["settings"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from pathlib import Path

        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        # Check for config file locations
        project_config = config.cwd / ".pichu" / "config.toml"
        global_config = Path.home() / ".pichu" / "config.toml"

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Setting", style="dim", min_width=28)
        table.add_column("Value", style="accent", min_width=35)

        # LLM settings
        table.add_row("[bold]LLM[/bold]", "")
        table.add_row("  model", config.model)
        table.add_row("  base_url", config.base_url or "[dim]not set[/dim]")
        table.add_row(
            "  api_key",
            ("[success]✓ set[/success]" if config.api_key else "[error]✗ not set[/error]"),
        )
        table.add_row("", "")

        # Limits
        table.add_row("[bold]Limits[/bold]", "")
        table.add_row("  context_window", f"{config.limits.context_window:,}")
        table.add_row("  compression_threshold", f"{config.limits.compression_threshold}")
        table.add_row("  max_tool_output_tokens", f"{config.limits.tool_output_display_tokens:,}")
        table.add_row("", "")

        # Shell
        table.add_row("[bold]Shell[/bold]", "")
        table.add_row("  default_timeout", f"{config.shell.security.default_timeout}s")
        table.add_row("  max_timeout", f"{config.shell.security.max_timeout}s")
        table.add_row("", "")

        # Approval
        table.add_row("[bold]Approval[/bold]", "")
        table.add_row("  policy", config.approval.policy.value)
        table.add_row("", "")

        # Memory
        table.add_row("[bold]Memory[/bold]", "")
        table.add_row("  enabled", str(config.memory.enabled))
        table.add_row("", "")

        # Hooks
        table.add_row("[bold]Hooks[/bold]", "")
        hooks_disabled = config.hooks.disabled if hasattr(config, "hooks") else True
        table.add_row("  disabled", str(hooks_disabled))
        table.add_row("", "")

        # Config file locations
        table.add_row("[bold]Config Files[/bold]", "")
        table.add_row(
            "  project",
            (str(project_config) if project_config.exists() else f"[dim]{project_config} (not found)[/dim]"),
        )
        table.add_row(
            "  global",
            (str(global_config) if global_config.exists() else f"[dim]{global_config} (not found)[/dim]"),
        )

        panel = Panel(
            table,
            title="[info]⚙ Configuration[/info]",
            border_style="border",
            padding=(1, 1),
        )

        renderables: list[object] = ["", panel, ""]

        # Offer to open in editor
        if args.strip() == "edit":
            import os
            import subprocess

            editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
            target = str(project_config) if project_config.exists() else str(global_config)
            if editor:
                try:
                    subprocess.Popen([editor, target])  # noqa: S603
                    renderables.append(f"  [dim]Opening {target} in {editor}...[/dim]")
                except Exception as e:
                    return CommandResult(error=f"Failed to open editor: {e}")
            else:
                renderables.append("  [dim]Set $EDITOR to open config in your editor. Use /config edit[/dim]")

        renderables.append("")
        return CommandResult(display=CommandDisplayPayload(renderables=renderables))

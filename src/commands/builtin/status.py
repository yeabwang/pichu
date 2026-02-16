"""Version, model, and connectivity status."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


def _get_version() -> str:
    try:
        return version("pichu")
    except PackageNotFoundError:
        return "0.1.0-dev"


class StatusCommand(SlashCommand):
    name = "status"
    description = "Version, model, and connectivity info"
    usage = "/status"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        import sys

        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Setting", style="dim", min_width=20)
        table.add_column("Value", style="accent", min_width=30)

        # App info
        table.add_row("Pichu version", _get_version())
        table.add_row(
            "Python",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
        table.add_row("", "")

        # Model info
        table.add_row("Model", config.model)
        table.add_row("Base URL", config.base_url or "[dim]not set[/dim]")
        table.add_row(
            "API key",
            ("[success]✓ set[/success]" if config.api_key else "[error]✗ not set[/error]"),
        )
        table.add_row("", "")

        # Working directory
        table.add_row("Working directory", str(config.cwd))
        table.add_row("", "")

        # Context window
        table.add_row("Context window", f"{config.limits.context_window:,} tokens")
        table.add_row("Compression threshold", f"{config.limits.compression_threshold * 100:.0f}%")
        table.add_row("", "")

        # Session info
        if session:
            table.add_row("Session ID", session.session_id[:16] + "...")
            table.add_row("Turns", str(session.turn_count))

        # MCP servers
        if session and session._mcp_manager:
            snapshots = session._mcp_manager.get_status_snapshot()
            if snapshots:
                for snapshot in snapshots:
                    if snapshot.status == "connected":
                        status_icon = "[success]✓[/success]"
                    elif snapshot.status == "disabled":
                        status_icon = "[dim]○[/dim]"
                    else:
                        status_icon = "[error]✗[/error]"
                    table.add_row(f"MCP: {snapshot.name}", f"{status_icon} {snapshot.status}")
            else:
                table.add_row("MCP servers", "[dim]none configured[/dim]")

        # API connectivity check
        api_status = "[success]✓ reachable[/success]"
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as http_client:
                base = config.base_url.rstrip("/") if config.base_url else ""
                if base:
                    resp = await http_client.get(
                        f"{base}/models",
                        headers={"Authorization": f"Bearer {config.api_key}"},
                    )
                    if resp.status_code < 500:
                        api_status = f"[success]✓ reachable[/success] (HTTP {resp.status_code})"
                    else:
                        api_status = f"[warning]⚠ HTTP {resp.status_code}[/warning]"
        except Exception:
            api_status = "[error]✗ unreachable[/error]"

        table.add_row("API connectivity", api_status)

        panel = Panel(
            table,
            title="[info]ℹ Status[/info]",
            border_style="border",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)
        tui.console.print()

        return CommandResult()

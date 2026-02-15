"""Manage MCP server connections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class McpCommand(SlashCommand):
    name = "mcp"
    description = "Manage MCP server connections"
    usage = "/mcp [status|reconnect]"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        if not session:
            return CommandResult(error="No active session.")

        action = args.strip().lower() if args.strip() else "status"
        mcp_manager = session._mcp_manager

        if action == "reconnect":
            return await self._reconnect(mcp_manager, session, tui)

        # Default: status
        snapshots = mcp_manager.get_status_snapshot()

        if not snapshots and not config.mcp_servers:
            tui.console.print()
            tui.console.print("  [dim]No MCP servers configured.[/dim]")
            tui.console.print("  [dim]Add servers to .pichu/config.toml under [mcp_servers][/dim]")
            tui.console.print()
            return CommandResult()

        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
            padding=(0, 2),
            border_style="border",
        )
        table.add_column("Server", style="accent", min_width=16)
        table.add_column("Status", min_width=14)
        table.add_column("Tools", justify="right", min_width=8)
        table.add_column("Transport", style="dim", min_width=12)

        # Show configured servers with their latest runtime status.
        for snapshot in snapshots:
            if snapshot.status == "disabled":
                status = "[dim]disabled[/dim]"
                tool_count = "-"
            elif snapshot.status == "connected":
                status = "[success]✓ connected[/success]"
                tool_count = str(snapshot.tool_count)
            elif snapshot.status == "error":
                error_hint = f" ({snapshot.error})" if snapshot.error else ""
                status = f"[error]✗ error{error_hint}[/error]"
                tool_count = str(snapshot.tool_count)
            else:
                status = f"[warning]{snapshot.status}[/warning]"
                tool_count = "-"

            table.add_row(snapshot.name, status, tool_count, snapshot.transport)

        # Show registered MCP tools
        tool_names = session.tool_registry.get_mcp_tool_names()

        panel = Panel(
            table,
            title="[info]🔌 MCP Servers[/info]",
            border_style="border",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)

        if tool_names:
            tui.console.print(f"  [dim]Registered MCP tools ({len(tool_names)}):[/dim]")
            for tn in tool_names[:15]:
                tui.console.print(f"    [dim]• {tn}[/dim]")
            if len(tool_names) > 15:
                tui.console.print(f"    [dim]... and {len(tool_names) - 15} more[/dim]")

        tui.console.print()
        tui.console.print("  [dim]/mcp reconnect — reconnect all servers[/dim]")
        tui.console.print()

        return CommandResult()

    async def _reconnect(self, mcp_manager, session: "Session", tui: "TUI") -> CommandResult:
        tui.console.print()
        tui.console.print("  [info]Reconnecting MCP servers...[/info]")

        try:
            count = await mcp_manager.reconnect(session.tool_registry)

            tui.console.print(f"  [success]✓[/success] Reconnected. {count} MCP tools registered.")
        except Exception as e:
            tui.console.print(f"  [error]✗ Reconnection failed: {e}[/error]")

        tui.console.print()
        return CommandResult()

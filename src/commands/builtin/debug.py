"""Troubleshoot and debug the current session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class DebugCommand(SlashCommand):
    name = "debug"
    description = "Troubleshoot current session"
    usage = "/debug [description]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich.panel import Panel

        if not session:
            return CommandResult(error="No active session.")

        cm = session.context_manager
        description = args.strip() if args.strip() else None

        lines = []

        # Session info
        lines.append("[bold]Session Info[/bold]")
        lines.append(f"  ID: {session.session_id}")
        lines.append(f"  Created: {session.created_at.isoformat()}")
        lines.append(f"  Turns: {session.turn_count}")
        lines.append(f"  Last updated: {session.updated_at.isoformat()}")
        lines.append("")

        # Context state
        if cm:
            msg_count = len(cm._messages)
            roles: dict[str, int] = {}
            for msg in cm._messages:
                roles[msg.role] = roles.get(msg.role, 0) + 1

            lines.append("[bold]Context State[/bold]")
            lines.append(f"  Messages: {msg_count}")
            for role, count in sorted(roles.items()):
                lines.append(f"    {role}: {count}")
            lines.append(f"  Latest tokens: {cm._latest_usage.total_tokens:,}")
            lines.append(f"  Total tokens: {cm._total_usage.total_tokens:,}")
            lines.append(f"  Context limit: {config.limits.context_window:,}")
            lines.append(f"  Needs compression: {cm.needs_compression()}")
            lines.append("")

        # Tool registry
        if session.tool_registry:
            tools = session.tool_registry.get_tools()
            mcp_tools = session.tool_registry.get_mcp_tool_names()
            lines.append("[bold]Tools[/bold]")
            lines.append(f"  Builtin tools: {len(tools) - len(mcp_tools)}")
            lines.append(f"  MCP tools: {len(mcp_tools)}")
            lines.append("")

        # Hooks
        if session.hook_engine and session.hook_engine.has_hooks:
            lines.append("[bold]Hooks[/bold]")
            lines.append(f"  Events: {len(session.hook_engine.get_registered_events())}")
            lines.append(f"  Handlers: {session.hook_engine.get_hook_count()}")
            lines.append("")

        # Memory
        if session.memory_manager:
            lines.append("[bold]Memory[/bold]")
            lines.append("  Manager: initialized")
            context = session.get_memory_context()
            lines.append(f"  Context block: {len(context) if context else 0} chars")
            lines.append("")

        # Sub-agents
        if session.subagent_loader:
            agents = session.subagent_loader.get_all()
            lines.append("[bold]Sub-Agents[/bold]")
            lines.append(f"  Loaded: {len(agents)}")
            for agent in agents[:5]:
                lines.append(f"    • {agent.name}")
            lines.append("")

        # Web budget
        budget = session.get_budget_status()
        if budget:
            lines.append("[bold]Web Budget[/bold]")
            for key, val in budget.items():
                lines.append(f"  {key}: {val}")
            lines.append("")

        # Last few messages (for debugging)
        if cm and cm._messages:
            lines.append("[bold]Recent Messages (last 5)[/bold]")
            for msg in cm._messages[-5:]:
                role = msg.role
                content_preview = (msg.content or "")[:80].replace("\n", "\\n")
                if len(msg.content or "") > 80:
                    content_preview += "..."
                tc = f" (tool_calls: {len(msg.tool_calls)})" if msg.tool_calls else ""
                lines.append(f"  [{role}]{tc} {content_preview}")
            lines.append("")

        if description:
            lines.append("[bold]Issue Description[/bold]")
            lines.append(f"  {description}")
            lines.append("")

        content = "\n".join(lines)

        panel = Panel(
            content,
            title="[info]🔍 Debug Info[/info]",
            border_style="border",
            padding=(1, 1),
        )

        return CommandResult(display=CommandDisplayPayload(renderables=["", panel, ""]))

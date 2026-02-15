"""List loaded sub-agents and their configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class AgentsCommand(SlashCommand):
    name = "agents"
    description = "List loaded sub-agents"
    usage = "/agents [name]"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        if not session:
            return CommandResult(error="No active session.")

        loader = session.subagent_loader
        if not loader:
            return CommandResult(error="Sub-agent system not initialized.")

        agents = loader.get_all()
        if not agents:
            tui.console.print()
            tui.console.print("[dim]No sub-agents loaded.[/dim]")
            tui.console.print("[dim]Add .md files to sub_agents/ or ~/.pichu/sub_agents/[/dim]")
            tui.console.print()
            return CommandResult()

        # If a name is given, show detail for that agent
        target = args.strip().lower() if args.strip() else None
        if target:
            agent = loader.get_by_name(target)
            if not agent:
                return CommandResult(error=f"Sub-agent '{target}' not found.")

            lines = []
            lines.append(f"[bold]{agent.name}[/bold]")
            lines.append(f"[dim]{agent.description}[/dim]")
            lines.append("")

            if agent.model:
                lines.append(f"[bold]Model:[/bold]   {agent.model}")
            else:
                lines.append(f"[bold]Model:[/bold]   [dim]inherited ({config.llm.model})[/dim]")

            if agent.tools:
                lines.append(f"[bold]Tools:[/bold]   {', '.join(agent.tools)}")
            else:
                lines.append("[bold]Tools:[/bold]   [dim]all inherited[/dim]")

            if agent.disallowed_tools:
                lines.append(f"[bold]Denied:[/bold]  {', '.join(agent.disallowed_tools)}")

            lines.append(f"[bold]Perms:[/bold]  {agent.permission_mode}")

            if agent.color:
                lines.append(f"[bold]Color:[/bold]   {agent.color}")

            if agent.source_path:
                lines.append(f"[bold]Source:[/bold]  {agent.source_path}")

            lines.append("")
            lines.append("[bold]System Prompt:[/bold]")
            # Show first 500 chars of system prompt
            prompt_preview = agent.system_prompt[:500]
            if len(agent.system_prompt) > 500:
                prompt_preview += "..."
            lines.append(prompt_preview)

            panel = Panel(
                "\n".join(lines),
                title=f"[info]Sub-Agent: {agent.name}[/info]",
                border_style="border",
                padding=(1, 1),
            )
            tui.console.print()
            tui.console.print(panel)
            tui.console.print()
            return CommandResult()

        # List all agents in a table
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold",
            padding=(0, 1),
        )
        table.add_column("Name", style="bold cyan")
        table.add_column("Description", max_width=50)
        table.add_column("Model", style="dim")
        table.add_column("Tools")
        table.add_column("Source", style="dim")

        for agent in agents:
            model = agent.model if agent.model else "[dim]inherited[/dim]"
            tools = str(len(agent.tools)) if agent.tools else "[dim]all[/dim]"
            source = "project" if agent.source_path and "sub_agents" in str(agent.source_path) else "user"
            # Determine if project-level or user-level
            if agent.source_path:
                proj_path = loader.project_agents_path
                if proj_path and str(agent.source_path).startswith(str(proj_path)):
                    source = "project"
                else:
                    source = "user"

            table.add_row(
                agent.name,
                agent.description[:50] + ("..." if len(agent.description) > 50 else ""),
                model,
                tools,
                source,
            )

        tui.console.print()
        tui.console.print(f"[bold]Sub-Agents ({len(agents)})[/bold]")
        tui.console.print(table)
        tui.console.print()
        tui.console.print("[dim]Use /agents <name> for details[/dim]")
        tui.console.print()

        return CommandResult()

"""Manage sub-agents and agent-level project scaffolding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand
from commands.builtin._scaffold import ensure_gitignore_entry

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class AgentsCommand(SlashCommand):
    name = "agent"
    description = "Manage sub-agents and AGENTS.md scaffolding"
    usage = "/agent [init|list|name]"
    aliases: list[str] = ["agents"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        normalized_args = args.strip()
        if normalized_args.lower() == "init":
            return await self._init_agent_files(tui, config)

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
            return CommandResult(
                display=CommandDisplayPayload(
                    renderables=[
                        "",
                        "[dim]No sub-agents loaded.[/dim]",
                        "[dim]Add .md files to sub_agents/ or ~/.pichu/sub_agents/[/dim]",
                        "",
                    ]
                )
            )

        # If a name is given, show detail for that agent
        target = normalized_args.lower() if normalized_args and normalized_args.lower() != "list" else None
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
            return CommandResult(display=CommandDisplayPayload(renderables=["", panel, ""]))

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
            source = "bundled"
            # Determine if bundled, project-level, or user-level
            if agent.source_path:
                proj_path = loader.project_agents_path
                user_path = loader.user_agents_path
                bundled_path = loader.bundled_agents_path
                if proj_path and str(agent.source_path).startswith(str(proj_path)):
                    source = "project"
                elif str(agent.source_path).startswith(str(user_path)):
                    source = "user"
                elif str(agent.source_path).startswith(str(bundled_path)):
                    source = "bundled"
                else:
                    source = "user"

            table.add_row(
                agent.name,
                agent.description[:50] + ("..." if len(agent.description) > 50 else ""),
                model,
                tools,
                source,
            )

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    "",
                    f"[bold]Sub-Agents ({len(agents)})[/bold]",
                    table,
                    "",
                    "[dim]Use /agent <name> for details[/dim]",
                    "",
                ]
            )
        )

    async def _init_agent_files(self, tui: "TUI", config: "Config") -> CommandResult:
        project_root = config.cwd
        memory_config = getattr(config, "memory", None)
        agents_file_name = getattr(memory_config, "project_agents_file", "AGENTS.md")
        local_agents_file_name = getattr(memory_config, "local_agents_file", "AGENTS.local.md")
        agents_file = project_root / agents_file_name
        local_file = project_root / local_agents_file_name

        created: list[str] = []
        if not agents_file.exists():
            project_name = project_root.name
            agents_file.write_text(_agents_template(project_name), encoding="utf-8")
            created.append(agents_file_name)

        if not local_file.exists():
            local_file.write_text(
                "# Local Instructions (gitignored)\n\n## Personal Preferences\n\n## Local Environment\n\n",
                encoding="utf-8",
            )
            created.append(local_agents_file_name)

        gitignore_updated = ensure_gitignore_entry(project_root, local_agents_file_name, "pichu local config")

        lines: list[str] = [""]
        if created:
            for item in created:
                lines.append(f"  [success]✓[/success] Created {item}")
        else:
            lines.append("  [dim]Agent scaffolding already initialized.[/dim]")

        if gitignore_updated:
            lines.append(f"  [success]✓[/success] Added {local_agents_file_name} to .gitignore")

        lines.append("")
        return CommandResult(display=CommandDisplayPayload(renderables=lines))


def _agents_template(project_name: str) -> str:
    return f"""# {project_name}

## Architecture
- Main runtime modules and boundaries:
-

## Tech Stack
- Languages, frameworks, and critical infrastructure:
-

## Patterns
- Reusable implementation patterns contributors should follow:
-

## Conventions
- Naming, formatting, and review expectations:
-

## Gotchas
- Known pitfalls, environment constraints, or unsafe operations:
-

## Testing
- Commands to run and quality gates before merge:
-
"""

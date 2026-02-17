"""Manage project memory stores and memory operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class MemoryCommand(SlashCommand):
    name = "memory"
    description = "Manage memory stores and AGENTS.md helpers"
    usage = "/memory [init|edit|list|search <query>]"
    aliases = ["mem"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        parts = args.strip().split(None, 1)
        action = parts[0].lower() if parts else "list"
        action_args = parts[1] if len(parts) > 1 else ""

        if action == "init":
            return await self._init_memory_storage(tui, config)

        # AGENTS.md files
        agents_loader = getattr(session, "agents_loader", None) if session else None
        memory_manager = getattr(session, "memory_manager", None) if session else None

        if action == "edit":
            return await self._edit_memory(tui, config)

        elif action == "search" and action_args:
            return await self._search_memory(action_args, memory_manager, tui)

        else:
            # Default: list memory sources
            return await self._list_memory(agents_loader, memory_manager, tui, config)

    async def _init_memory_storage(self, tui: "TUI", config: "Config") -> CommandResult:
        from pathlib import Path

        from commands.builtin._scaffold import relative_to_project
        from utils.memory_storage import CURSOR_FILE_NAME, STATE_FILE_NAME, ProjectMemoryStorage

        project_root = config.cwd
        memory_config = getattr(config, "memory", None)
        memory_dir = getattr(memory_config, "project_memory_dir", ".pichu/memory")
        agents_file = getattr(memory_config, "project_agents_file", "AGENTS.md")
        local_agents_file = getattr(memory_config, "local_agents_file", "AGENTS.local.md")

        storage_path = Path(memory_dir)
        if not storage_path.is_absolute():
            storage_path = project_root / storage_path
        storage_existed = storage_path.exists()

        storage = ProjectMemoryStorage(
            project_root,
            project_name=project_root.name,
            memory_dir=memory_dir,
            agents_file=agents_file,
            local_agents_file=local_agents_file,
        )

        created: list[str] = []
        if not storage_existed:
            created.append(f"{relative_to_project(storage_path, project_root)}/")

        state_path = storage_path / STATE_FILE_NAME
        if not state_path.exists():
            storage.save(storage.load())
            created.append(relative_to_project(state_path, project_root))

        cursor_path = storage_path / CURSOR_FILE_NAME
        if not cursor_path.exists():
            storage.set_cursor({"last_processed": None, "position": 0})
            created.append(relative_to_project(cursor_path, project_root))

        tui.console.print()
        if created:
            for item in created:
                tui.console.print(f"  [success]✓[/success] Created {item}")
        else:
            tui.console.print("  [dim]Memory scaffolding already initialized.[/dim]")
        tui.console.print()
        return CommandResult()

    async def _list_memory(self, agents_loader, memory_manager, tui: "TUI", config: "Config") -> CommandResult:
        from pathlib import Path

        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 2))
        table.add_column("Source", style="accent", min_width=18)
        table.add_column("Path", style="dim", min_width=35)
        table.add_column("Status", min_width=12)

        # AGENTS.md hierarchy
        project_root = config.cwd
        global_agents = Path.home() / ".pichu" / "AGENTS.md"
        project_agents = project_root / "AGENTS.md"
        local_agents = project_root / "AGENTS.local.md"

        for label, path in [
            ("Global AGENTS.md", global_agents),
            ("Project AGENTS.md", project_agents),
            ("Local AGENTS.md", local_agents),
        ]:
            exists = path.exists()
            status = "[success]✓ loaded[/success]" if exists else "[dim]not found[/dim]"
            size = ""
            if exists:
                size_bytes = path.stat().st_size
                size = f" ({size_bytes:,} bytes)"
            table.add_row(label, f"{path}{size}", status)

        # Memory system
        if memory_manager:
            try:
                gs = memory_manager.global_store
                global_count = len(gs.memories) if gs else 0
            except Exception:
                global_count = 0
            try:
                ps = memory_manager.project_store
                project_count = len(ps.memories) if ps else 0
            except Exception:
                project_count = 0
            table.add_row("", "", "")
            table.add_row(
                "Global memories",
                (
                    str(memory_manager._global_storage.storage_path)
                    if hasattr(memory_manager, "_global_storage")
                    else "~"
                ),
                f"{global_count} entries",
            )
            table.add_row(
                "Project memories",
                (
                    str(memory_manager._project_storage.storage_path)
                    if hasattr(memory_manager, "_project_storage") and memory_manager._project_storage
                    else "~"
                ),
                f"{project_count} entries",
            )

        panel = Panel(
            table,
            title="[info]🧠 Memory & Context[/info]",
            border_style="border",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)
        tui.console.print()
        tui.console.print("  [dim]/memory edit — open AGENTS.md in editor[/dim]")
        tui.console.print("  [dim]/memory search <query> — search memories[/dim]")
        tui.console.print()

        return CommandResult()

    async def _edit_memory(self, tui: "TUI", config: "Config") -> CommandResult:
        import os
        import subprocess

        project_agents = config.cwd / "AGENTS.md"

        # Create if not exists
        if not project_agents.exists():
            project_agents.write_text(
                "# Project Instructions\n\n## Architecture\n\n## Patterns\n\n## Conventions\n\n## Gotchas\n\n",
                encoding="utf-8",
            )
            tui.console.print(f"  [success]✓[/success] Created {project_agents}")

        editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
        if editor:
            try:
                subprocess.Popen([editor, str(project_agents)])  # noqa: S603
                tui.console.print(f"  [dim]Opening {project_agents} in {editor}...[/dim]")
            except Exception as e:
                return CommandResult(error=f"Failed to open editor: {e}")
        else:
            tui.console.print(f"  [dim]AGENTS.md path: {project_agents}[/dim]")
            tui.console.print("  [dim]Set $EDITOR to open in your editor, or edit manually.[/dim]")

        return CommandResult()

    async def _search_memory(self, query: str, memory_manager, tui: "TUI") -> CommandResult:
        if not memory_manager:
            return CommandResult(error="Memory system not initialized.")

        try:
            results = memory_manager.search(query)
            if not results:
                tui.console.print(f"  [dim]No memories matching '{query}'[/dim]")
                return CommandResult()

            tui.console.print()
            tui.console.print(f"  [info]Found {len(results)} memories matching '{query}':[/info]")
            for mem in results[:10]:
                cat = getattr(mem.category, "value", str(mem.category))
                content = str(mem.content)[:100]
                tui.console.print(f"  [dim]•[/dim] [{cat}] {content}")
            tui.console.print()
        except Exception as e:
            return CommandResult(error=f"Memory search failed: {e}")

        return CommandResult()

"""List and manage tasks from the task manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class TasksCommand(SlashCommand):
    name = "tasks"
    description = "List and manage tasks"
    usage = "/tasks [list|summary]"
    aliases = ["task"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        if not session:
            return CommandResult(error="No active session.")

        tm = session.task_manager
        if not tm:
            return CommandResult(error="Task manager not initialized.")

        subcommand = args.strip().lower() if args.strip() else "list"

        if subcommand == "summary":
            summary = session.get_task_summary()
            if not summary:
                tui.console.print()
                tui.console.print("[dim]No tasks in current list.[/dim]")
                tui.console.print()
                return CommandResult()

            panel = Panel(
                summary,
                title="[info]📋 Task Summary[/info]",
                border_style="border",
                padding=(1, 1),
            )
            tui.console.print()
            tui.console.print(panel)
            tui.console.print()
            return CommandResult()

        # Default: list all tasks
        tasks = list(tm.get_all_tasks())
        if not tasks:
            tui.console.print()
            tui.console.print(f"[dim]No tasks in list '{tm.list_id}'.[/dim]")
            tui.console.print("[dim]Tasks are created by the agent during conversations.[/dim]")
            tui.console.print()
            return CommandResult()

        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold",
            padding=(0, 1),
        )
        table.add_column("ID", style="dim", max_width=12)
        table.add_column("Status")
        table.add_column("Subject", max_width=40)
        table.add_column("Owner", style="dim")
        table.add_column("Deps", style="dim")

        status_style = {
            "pending": "[yellow]● pending[/yellow]",
            "in_progress": "[blue]▶ in_progress[/blue]",
            "completed": "[green]✓ completed[/green]",
        }

        for task in tasks:
            status = status_style.get(task.status, task.status)
            tid = task.id[:10] + "…" if len(task.id) > 10 else task.id
            owner = task.owner or "[dim]—[/dim]"
            deps = ""
            if task.blocked_by:
                deps = f"⊳ {len(task.blocked_by)}"
            if task.blocks:
                deps += f" ⊲ {len(task.blocks)}" if deps else f"⊲ {len(task.blocks)}"

            table.add_row(
                tid,
                status,
                task.subject[:40] + ("…" if len(task.subject) > 40 else ""),
                owner,
                deps or "[dim]—[/dim]",
            )

        # Count by status
        pending = sum(1 for t in tasks if t.status == "pending")
        in_progress = sum(1 for t in tasks if t.status == "in_progress")
        completed = sum(1 for t in tasks if t.status == "completed")

        tui.console.print()
        tui.console.print(
            f"[bold]Tasks ({len(tasks)})[/bold]  "
            f"[yellow]{pending} pending[/yellow]  "
            f"[blue]{in_progress} active[/blue]  "
            f"[green]{completed} done[/green]"
        )
        tui.console.print(f"[dim]List: {tm.list_id}[/dim]")
        tui.console.print(table)
        tui.console.print()

        return CommandResult()

"""Slash command: /rewind — revert to a previous checkpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class RewindCommand(SlashCommand):
    name = "rewind"
    description = "Rewind to a previous checkpoint"
    usage = "/rewind [turn_number]"
    aliases = ["undo"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich.prompt import Prompt
        from rich.table import Table

        cp_mgr = session.checkpoint_manager
        if not cp_mgr:
            return CommandResult(error="Checkpoints are not enabled. Check your config.")

        checkpoints = cp_mgr.get_checkpoints()
        if not checkpoints:
            tui.console.print("[dim]No checkpoints available yet.[/dim]")
            return CommandResult()

        # If a turn number was specified, go directly to it
        target_turn: int | None = None
        if args.strip().isdigit():
            target_turn = int(args.strip())
        else:
            # Show checkpoint list and let user pick
            table = Table(title="Available Checkpoints", show_lines=False)
            table.add_column("Turn", style="cyan", justify="right")
            table.add_column("Prompt", style="bold")
            table.add_column("Files Changed", justify="right")
            table.add_column("Time", style="dim")

            for cp in checkpoints:
                # Format timestamp
                try:
                    from datetime import datetime

                    ts = datetime.fromisoformat(cp.timestamp)
                    time_str = ts.strftime("%H:%M:%S")
                except (ValueError, TypeError):
                    time_str = ""

                table.add_row(
                    str(cp.turn_number),
                    cp.user_message[:60] + ("..." if len(cp.user_message) > 60 else ""),
                    str(len(cp.file_paths)),
                    time_str,
                )

            tui.console.print(table)
            tui.console.print()

            try:
                choice = Prompt.ask(
                    "[bold]Rewind to turn[/bold]",
                    console=tui.console,
                    default=str(checkpoints[-1].turn_number),
                )
                if not choice.strip().isdigit():
                    tui.console.print("[dim]Cancelled.[/dim]")
                    return CommandResult()
                target_turn = int(choice.strip())
            except (KeyboardInterrupt, EOFError):
                tui.console.print("[dim]Cancelled.[/dim]")
                return CommandResult()

        # Validate turn number
        valid_turns = [cp.turn_number for cp in checkpoints]
        if target_turn not in valid_turns:
            return CommandResult(error=f"Turn {target_turn} not found. Valid turns: {valid_turns}")

        # Ask what to restore
        tui.console.print()
        tui.console.print("[bold]Rewind options:[/bold]")
        tui.console.print("  [cyan]1[/cyan] — Restore code only (revert file changes)")
        tui.console.print("  [cyan]2[/cyan] — Restore conversation only (truncate context)")
        tui.console.print("  [cyan]3[/cyan] — Restore both (code + conversation)")
        tui.console.print("  [cyan]4[/cyan] — Summarize from here (compact history after this point)")
        tui.console.print()

        try:
            mode = Prompt.ask(
                "[bold]Choose[/bold]",
                choices=["1", "2", "3", "4"],
                default="3",
                console=tui.console,
            )
        except (KeyboardInterrupt, EOFError):
            tui.console.print("[dim]Cancelled.[/dim]")
            return CommandResult()

        results: list[str] = []

        # Restore code
        if mode in ("1", "3"):
            restored_files = cp_mgr.restore_code(target_turn)
            if restored_files:
                results.append(f"Restored {len(restored_files)} file(s)")
                for f in restored_files[:5]:
                    tui.console.print(f"  [green]↩[/green] {f}")
                if len(restored_files) > 5:
                    tui.console.print(f"  [dim]...and {len(restored_files) - 5} more[/dim]")
            else:
                results.append("No files needed restoring")

        # Restore conversation
        if mode in ("2", "3"):
            msg_index = cp_mgr.get_message_index_at_turn(target_turn)
            if msg_index is not None and session.context_manager:
                session.context_manager.truncate_to(msg_index)
                results.append(f"Conversation rewound to message index {msg_index}")

                # Truncate transcript on disk too
                if session.storage:
                    # Count transcript entries up to target turn
                    entries = session.storage.load_transcript(session.session_id)
                    keep_count = sum(1 for e in entries if e.turn <= target_turn)
                    session.storage.truncate_transcript(session.session_id, keep_count)
                    results.append(f"Transcript truncated to {keep_count} entries")
            else:
                results.append("Could not determine message index for conversation rewind")

        # Summarize from here
        if mode == "4":
            results.append("Summarize-from-here: will compact context on next turn")
            # Force compression on next turn by setting a low token count
            if session.context_manager:
                # Inject a system note that will trigger compaction
                inject = (
                    f"[System: The user requested to rewind context to turn {target_turn}. "
                    f"Please summarize all work done after turn {target_turn} and focus on "
                    f"continuing from that point.]"
                )
                return CommandResult(inject_prompt=inject)

        tui.console.print()
        for r in results:
            tui.console.print(f"[green]✓[/green] {r}")

        return CommandResult()

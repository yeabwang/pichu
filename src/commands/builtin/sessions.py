"""Slash command: /sessions — list and manage saved sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand
from utils.session_storage import (
    SessionReferenceAmbiguousError,
    SessionReferenceNotFoundError,
)

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class SessionsCommand(SlashCommand):
    name = "sessions"
    description = "List and manage saved sessions"
    usage = "/sessions [resume <id> | delete <id> | all | <limit>]"
    aliases = ["session"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from datetime import datetime

        from rich.table import Table

        storage = session.storage
        if not storage:
            return CommandResult(error="Session persistence is not enabled. Check your config.")

        parts = args.strip().split(None, 1)
        subcommand = parts[0].lower() if parts else ""
        cwd = str(config.cwd) if hasattr(config, "cwd") else None

        # --- /sessions resume <id> ---
        if subcommand == "resume":
            if len(parts) < 2 or not parts[1].strip():
                return CommandResult(error="Usage: /sessions resume <session-id>")
            target_id = parts[1].strip()
            return self._handle_resume(target_id, storage, tui, cwd)

        # --- /sessions delete <id> ---
        if subcommand in ("delete", "rm", "remove"):
            if len(parts) < 2 or not parts[1].strip():
                return CommandResult(error="Usage: /sessions delete <session-id>")
            target_id = parts[1].strip()
            return self._handle_delete(target_id, session, storage, tui, cwd)

        # --- /sessions all (include empty sessions) ---
        show_empty = subcommand == "all"

        # --- /sessions [limit] ---
        limit = 20
        if subcommand.isdigit():
            limit = int(subcommand)

        sessions = storage.list_sessions(cwd=cwd, limit=limit)

        # Filter out 0-turn empty sessions (unless 'all')
        if not show_empty:
            current_id = session.session_id
            sessions = [m for m in sessions if m.turn_count > 0 or m.session_id == current_id]

        if not sessions:
            return CommandResult(
                display=CommandDisplayPayload(
                    renderables=["[dim]No saved sessions found. Use /sessions all to include empty ones.[/dim]"]
                )
            )

        table = Table(title="Saved Sessions", show_lines=False)
        table.add_column("ID", style="cyan", max_width=10)
        table.add_column("Title", style="bold")
        table.add_column("Model", style="dim")
        table.add_column("Turns", justify="right")
        table.add_column("Updated", style="dim")
        table.add_column("Branch", style="green")

        current_id = session.session_id

        for meta in sessions:
            short_id = meta.session_id[:8]
            is_current = meta.session_id == current_id
            marker = " [green]◄[/green]" if is_current else ""

            # Format updated_at as relative time
            try:
                updated = datetime.fromisoformat(meta.updated_at)
                delta = datetime.now() - updated
                if delta.days > 0:
                    updated_str = f"{delta.days}d ago"
                elif delta.seconds > 3600:
                    updated_str = f"{delta.seconds // 3600}h ago"
                elif delta.seconds > 60:
                    updated_str = f"{delta.seconds // 60}m ago"
                else:
                    updated_str = "just now"
            except (ValueError, TypeError):
                updated_str = "unknown"

            table.add_row(
                short_id + marker,
                meta.title or "[dim]untitled[/dim]",
                meta.model or "",
                str(meta.turn_count),
                updated_str,
                meta.branch or "",
            )

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    table,
                    (
                        "[dim]Commands: /sessions resume <id> · /sessions delete <id> · "
                        "/sessions all · --continue / --resume <id>[/dim]"
                    ),
                ]
            )
        )

    def _handle_resume(self, target_id: str, storage, tui, cwd: str | None) -> CommandResult:
        """Handle /sessions resume <id>."""
        try:
            match = storage.resolve_session_reference(target_id, cwd=cwd)
        except SessionReferenceNotFoundError as exc:
            return CommandResult(error=str(exc))
        except SessionReferenceAmbiguousError as exc:
            return CommandResult(error=self._format_ambiguity(exc))

        if match.turn_count == 0:
            return CommandResult(error=f"Session {target_id[:8]} is empty (0 turns). Nothing to resume.")

        return CommandResult(
            display=CommandDisplayPayload(
                renderables=[
                    "[yellow]To resume this session, restart with:[/yellow]",
                    f"  [bold]pichu --resume {match.session_id[:8]}[/bold]",
                    "[dim]Mid-session resume is not yet supported. Exit first, then use the flag above.[/dim]",
                ]
            )
        )

    def _handle_delete(self, target_id: str, session, storage, tui, cwd: str | None) -> CommandResult:
        """Handle /sessions delete <id>."""
        try:
            match = storage.resolve_session_reference(target_id, cwd=cwd)
        except SessionReferenceNotFoundError as exc:
            return CommandResult(error=str(exc))
        except SessionReferenceAmbiguousError as exc:
            return CommandResult(error=self._format_ambiguity(exc))

        if match.session_id == session.session_id:
            return CommandResult(error="Cannot delete the current active session.")

        deleted = storage.delete_session(match.session_id)
        if deleted:
            title = match.title or "untitled"
            return CommandResult(
                display=CommandDisplayPayload(
                    renderables=[f"[green]✓[/green] Deleted session [cyan]{match.session_id[:8]}[/cyan] ({title})"]
                )
            )
        else:
            return CommandResult(error=f"Failed to delete session {target_id[:8]}.")

    def _format_ambiguity(self, exc: SessionReferenceAmbiguousError) -> str:
        sample = ", ".join(meta.session_id[:8] for meta in exc.matches[:5])
        suffix = "..." if len(exc.matches) > 5 else ""
        return f"{exc}. Matches: {sample}{suffix}"

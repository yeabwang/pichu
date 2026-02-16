"""Compact conversation context with optional focus instructions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class CompactCommand(SlashCommand):
    name = "compact"
    description = "Compact context with optional focus instructions"
    usage = "/compact [instructions]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session or not session.context_manager:
            return CommandResult(error="No active session.")

        compactor = session.chat_compactor
        context_manager = session.context_manager

        tui.console.print()
        tui.console.print("  [info]⚡ Compacting context...[/info]")

        # Get current token count
        max_tokens = int(config.limits.context_window * config.limits.compression_threshold * 0.5)

        try:
            summary, usage, original_tokens = await compactor.compact_context(
                context_manager=context_manager,
                max_tokens=max_tokens,
                system_prompt=args.strip() if args.strip() else None,
            )

            if summary is None:
                tui.console.print("  [dim]Context is already compact — no action needed.[/dim]")
                tui.console.print()
                return CommandResult()

            # Apply the compacted summary
            context_manager.replace_with_summary(summary)

            new_tokens = context_manager.get_message_token_total()

            tui.print_context_event(
                "compacted",
                {
                    "original_tokens": original_tokens,
                    "new_tokens": new_tokens,
                    "tokens_saved": original_tokens - new_tokens,
                },
            )

            if args.strip():
                tui.console.print(f"  [dim]Focus: {args.strip()}[/dim]")

            tui.console.print()

        except Exception as e:
            return CommandResult(error=f"Compaction failed: {e}")

        return CommandResult()

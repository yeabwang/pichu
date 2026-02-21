"""Compact conversation context with optional focus instructions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

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

        # Get current token count
        max_tokens = int(config.limits.context_window * config.limits.compression_threshold * 0.5)

        try:
            summary, usage, original_tokens = await compactor.compact_context(
                context_manager=context_manager,
                max_tokens=max_tokens,
                system_prompt=args.strip() if args.strip() else None,
            )

            if summary is None:
                return CommandResult(
                    display=CommandDisplayPayload(
                        renderables=[
                            "",
                            "  [info]⚡ Compacting context...[/info]",
                            "  [dim]Context is already compact — no action needed.[/dim]",
                            "",
                        ]
                    )
                )

            # Apply the compacted summary
            context_manager.replace_with_summary(summary)

            new_tokens = context_manager.get_message_token_total()

            lines: list[str] = [
                "",
                "  [info]⚡ Compacting context...[/info]",
                f"  [warning]⚡ Context compacted:[/warning] [dim]{original_tokens:,} → {new_tokens:,} tokens[/dim] "
                f"[success](saved {original_tokens - new_tokens:,})[/success]",
            ]
            if args.strip():
                lines.append(f"  [dim]Focus: {args.strip()}[/dim]")
            lines.append("")
            return CommandResult(display=CommandDisplayPayload(renderables=lines))

        except Exception as e:
            return CommandResult(error=f"Compaction failed: {e}")

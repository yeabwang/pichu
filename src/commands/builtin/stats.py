"""Usage visualizations — turns, tokens, time, model breakdown."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand
from utils.text import count_tokens

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class StatsCommand(SlashCommand):
    name = "stats"
    description = "Usage visualizations (tokens, turns, duration)"
    usage = "/stats"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session:
            return CommandResult(error="No active session.")

        from rich import box
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        # Session duration
        now = datetime.now()
        duration = now - session.created_at
        minutes = int(duration.total_seconds() // 60)
        seconds = int(duration.total_seconds() % 60)
        duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

        cm = session.context_manager
        total_usage = cm._total_usage if cm else None
        context_stats = None
        if cm:
            tool_schema_tokens = 0
            if session and session.tool_registry:
                tool_schema = session.tool_registry.get_schema()
                if tool_schema:
                    tool_schema_tokens = count_tokens(json.dumps(tool_schema), config.model)
            context_stats = cm.get_context_stats(tool_schema_tokens=tool_schema_tokens)
            tui.update_context_tracker(context_stats)

        # Gather stats
        turns = session.turn_count
        total_tokens = total_usage.total_tokens if total_usage else 0
        input_tokens = total_usage.prompt_tokens if total_usage else 0
        output_tokens = total_usage.completion_tokens if total_usage else 0
        cached_tokens = total_usage.cache_tokens if total_usage else 0

        # Message counts
        msg_counts = {"user": 0, "assistant": 0, "tool": 0}
        if cm:
            for msg in cm._messages:
                if msg.role in msg_counts:
                    msg_counts[msg.role] += 1

        # Tool call count (approximate from tool result messages)
        tool_calls = msg_counts["tool"]

        # Token rate
        total_secs = max(duration.total_seconds(), 1)
        tokens_per_min = int(total_tokens / (total_secs / 60))

        # Build stats table
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Metric", style="dim", min_width=22)
        table.add_column("Value", style="accent", justify="right", min_width=16)

        table.add_row("Session duration", duration_str)
        table.add_row("Session ID", session.session_id[:12] + "...")
        table.add_row("", "")
        table.add_row("Turns", str(turns))
        table.add_row("Tool calls", str(tool_calls))
        table.add_row(
            "Messages",
            f"{msg_counts['user']}u / {msg_counts['assistant']}a / {msg_counts['tool']}t",
        )
        table.add_row("", "")

        def fmt(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.2f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)

        table.add_row("Total tokens", fmt(total_tokens))
        table.add_row("  Input", fmt(input_tokens))
        table.add_row("  Output", fmt(output_tokens))
        table.add_row("  Cached", fmt(cached_tokens))
        if context_stats:
            ctx_limit = context_stats["context_limit"]

            def ctx_sub_pct(n: int) -> str:
                p = round(n / ctx_limit * 100, 1) if ctx_limit > 0 else 0
                return f"{p:.1f}%"

            table.add_row("", "")
            table.add_row(
                "Context window",
                f"{fmt(context_stats['total_tokens'])} / {fmt(ctx_limit)} tokens • {context_stats['percentage']:.1f}%",
            )
            table.add_row(
                "  System",
                f"{fmt(context_stats['system_tokens'])} ({ctx_sub_pct(context_stats['system_tokens'])})",
            )
            table.add_row(
                "  Tool defs",
                f"{fmt(context_stats['tool_def_tokens'])} ({ctx_sub_pct(context_stats['tool_def_tokens'])})",
            )
            table.add_row(
                "  User msgs",
                f"{fmt(context_stats['user_tokens'])} ({ctx_sub_pct(context_stats['user_tokens'])})",
            )
            table.add_row(
                "  Assistant",
                f"{fmt(context_stats['assistant_tokens'])} ({ctx_sub_pct(context_stats['assistant_tokens'])})",
            )
            table.add_row(
                "  Tool results",
                f"{fmt(context_stats['tool_result_tokens'])} ({ctx_sub_pct(context_stats['tool_result_tokens'])})",
            )
            table.add_row("Context tracker", "[success]active[/success]")
        table.add_row("", "")
        table.add_row("Tokens/min", fmt(tokens_per_min))
        table.add_row("Model", config.model)

        # Token distribution bar
        bar_width = 30
        if total_tokens > 0:
            in_pct = input_tokens / total_tokens
            out_pct = output_tokens / total_tokens
            cached_pct = cached_tokens / total_tokens
        else:
            in_pct = out_pct = cached_pct = 0

        bar = Text("  ")
        bar.append("█" * max(1, int(bar_width * in_pct)), style="tokens.input")
        bar.append("█" * max(1, int(bar_width * out_pct)), style="tokens.output")
        if cached_tokens > 0:
            bar.append("█" * max(1, int(bar_width * cached_pct)), style="tokens.cached")
        bar.append("  ")
        bar.append("input", style="tokens.input")
        bar.append(" / ", style="dim")
        bar.append("output", style="tokens.output")
        bar.append(" / ", style="dim")
        bar.append("cached", style="tokens.cached")

        panel = Panel(
            table,
            title="[info]📈 Session Statistics[/info]",
            border_style="border",
            padding=(1, 1),
        )

        return CommandResult(display=CommandDisplayPayload(renderables=["", panel, bar, ""]))

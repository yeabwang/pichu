"""Visualize context token distribution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ContextCommand(SlashCommand):
    name = "context"
    description = "Visualize context token usage"
    usage = "/context"
    aliases = ["ctx"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session or not session.context_manager:
            return CommandResult(error="No active session.")

        from rich import box
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        cm = session.context_manager
        tool_schema_tokens = 0  # Could be computed from tool definitions

        stats = cm.get_context_stats(tool_schema_tokens=tool_schema_tokens)

        total = stats["total_tokens"]
        limit = stats["context_limit"]
        pct = stats["percentage"]

        # Build a visual bar
        bar_width = 40
        filled = int(bar_width * pct / 100) if limit > 0 else 0
        filled = min(filled, bar_width)

        if pct >= 80:
            bar_style = "context.utilization.high"
        elif pct >= 60:
            bar_style = "context.utilization.medium"
        else:
            bar_style = "context.utilization.low"

        bar = Text()
        bar.append("  [", style="dim")
        bar.append("█" * filled, style=bar_style)
        bar.append("░" * (bar_width - filled), style="dim")
        bar.append("]", style="dim")
        bar.append(f" {pct}%", style=bar_style)

        # Breakdown table
        def fmt(n: int) -> str:
            if n >= 1000:
                return f"{n / 1000:.1f}K"
            return str(n)

        def sub_pct(n: int) -> str:
            p = round(n / limit * 100, 1) if limit > 0 else 0
            return f"{p}%"

        categories = [
            ("System prompt", stats["system_tokens"], "tokens.input"),
            ("Tool definitions", stats["tool_def_tokens"], "tokens.input"),
            ("User messages", stats["user_tokens"], "tokens.output"),
            ("Assistant messages", stats["assistant_tokens"], "tokens.output"),
            ("Tool results", stats["tool_result_tokens"], "tokens.cached"),
        ]

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Category", style="dim", min_width=20)
        table.add_column("Tokens", justify="right", min_width=8)
        table.add_column("%", justify="right", min_width=6)
        table.add_column("Bar", min_width=20)

        for label, tokens, style in categories:
            cat_pct = (tokens / limit * 100) if limit > 0 else 0
            cat_bar_len = int(20 * cat_pct / 100)
            cat_bar = Text()
            cat_bar.append("▓" * cat_bar_len, style=style)
            cat_bar.append("░" * (20 - cat_bar_len), style="dim")
            table.add_row(label, fmt(tokens), sub_pct(tokens), cat_bar)

        # Message count breakdown
        msg_counts = {"user": 0, "assistant": 0, "tool": 0}
        for msg in cm._messages:
            if msg.role in msg_counts:
                msg_counts[msg.role] += 1

        panel = Panel(
            renderable=table,
            title=f"[info]📊 Context Window — {fmt(total)} / {fmt(limit)} tokens[/info]",
            subtitle=Text(
                f"{msg_counts['user']} user · {msg_counts['assistant']} assistant · {msg_counts['tool']} tool messages",
                style="dim",
            ),
            subtitle_align="right",
            border_style="border",
            padding=(1, 1),
        )

        return CommandResult(display=CommandDisplayPayload(renderables=["", bar, panel, ""]))

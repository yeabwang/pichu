"""Token usage and cost statistics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


# Rough pricing per 1M tokens (USD) — adjust for your provider
_PRICING = {
    "input": 0.15,  # per 1M input tokens
    "output": 0.60,  # per 1M output tokens
    "cached": 0.075,  # per 1M cached tokens
}


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class CostCommand(SlashCommand):
    name = "cost"
    description = "Token usage and cost statistics"
    usage = "/cost"
    aliases = ["usage"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session or not session.context_manager:
            return CommandResult(error="No active session.")

        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        cm = session.context_manager
        total = cm._total_usage
        latest = cm._latest_usage

        # Compute cost estimate
        input_cost = (total.prompt_tokens / 1_000_000) * _PRICING["input"]
        output_cost = (total.completion_tokens / 1_000_000) * _PRICING["output"]
        cached_cost = (total.cache_tokens / 1_000_000) * _PRICING["cached"]
        total_cost = input_cost + output_cost + cached_cost

        table = Table(
            box=box.SIMPLE,
            show_header=True,
            header_style="bold",
            padding=(0, 2),
        )
        table.add_column("Metric", style="dim", min_width=22)
        table.add_column("This Turn", style="tokens.input", justify="right", min_width=12)
        table.add_column("Session Total", style="accent", justify="right", min_width=14)

        table.add_row(
            "Input tokens",
            _fmt_tokens(latest.prompt_tokens),
            _fmt_tokens(total.prompt_tokens),
        )
        table.add_row(
            "Output tokens",
            _fmt_tokens(latest.completion_tokens),
            _fmt_tokens(total.completion_tokens),
        )
        table.add_row(
            "Cached tokens",
            _fmt_tokens(latest.cache_tokens),
            _fmt_tokens(total.cache_tokens),
        )
        table.add_row("", "", "")
        table.add_row(
            "Total tokens",
            _fmt_tokens(latest.total_tokens),
            _fmt_tokens(total.total_tokens),
        )
        table.add_row("", "", "")
        table.add_row(
            "Estimated cost",
            "",
            f"${total_cost:.4f}",
        )

        panel = Panel(
            table,
            title="[info]💰 Token Usage & Cost[/info]",
            border_style="border",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)

        # Budget status if available
        budget = session.get_budget_status()
        if budget:
            tui.console.print(
                f"  [dim]Web budget: searches {budget['searches']} · "
                f"fetches {budget['fetches']} · bytes {budget['bytes']}[/dim]"
            )

        tui.console.print(f"  [dim]Turns: {session.turn_count}  ·  Model: {config.model}[/dim]")
        tui.console.print()

        return CommandResult()

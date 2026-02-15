"""Show and manage approval / permission settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class PermissionsCommand(SlashCommand):
    name = "permissions"
    description = "Show approval policy and permission rules"
    usage = "/permissions"
    aliases = ["perms"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich.panel import Panel

        if not session:
            return CommandResult(error="No active session.")

        am = session.approval_manager
        approval_cfg = config.approval

        # --- Policy ---
        lines = []
        lines.append(f"[bold]Approval Policy:[/bold]  {am.policy.value}")
        lines.append("")

        # Policy descriptions
        descriptions = {
            "yolo": "Auto-approve everything (no confirmations)",
            "auto_edit": "Auto-approve file edits, prompt for shell/network",
            "auto": "Auto-approve safe commands, prompt for unknown",
            "on_request": "Prompt for all mutating tools (default)",
            "on_failure": "Like auto, but prompt again on failure",
            "plan": "Read-only mode — deny all mutations",
            "never": "Deny everything not explicitly allowed",
        }
        desc = descriptions.get(am.policy.value, "Custom policy")
        lines.append(f"  [dim]{desc}[/dim]")
        lines.append("")

        # --- Permission rules ---
        rules = getattr(approval_cfg, "rules", None)
        if rules:
            if rules.deny:
                lines.append("[bold red]Deny Rules:[/bold red]")
                for r in rules.deny:
                    lines.append(f"  ✗ {r}")
                lines.append("")

            if rules.ask:
                lines.append("[bold yellow]Ask Rules:[/bold yellow]")
                for r in rules.ask:
                    lines.append(f"  ? {r}")
                lines.append("")

            if rules.allow:
                lines.append("[bold green]Allow Rules:[/bold green]")
                for r in rules.allow:
                    lines.append(f"  ✓ {r}")
                lines.append("")
        else:
            lines.append("[dim]No permission rules configured.[/dim]")
            lines.append("")

        # --- Session memory ---
        approvals = len(am._session_approvals)
        denials = len(am._session_denials)
        lines.append("[bold]Session Memory:[/bold]")
        lines.append(f"  Remembered approvals: {approvals}")
        lines.append(f"  Remembered denials:   {denials}")

        if am._session_approvals:
            lines.append("")
            lines.append("  [green]Approved:[/green]")
            for key in sorted(am._session_approvals):
                lines.append(f"    ✓ {key}")

        if am._session_denials:
            lines.append("")
            lines.append("  [red]Denied:[/red]")
            for key in sorted(am._session_denials):
                lines.append(f"    ✗ {key}")

        content = "\n".join(lines)

        panel = Panel(
            content,
            title="[info]🔒 Permissions[/info]",
            border_style="border",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)
        tui.console.print()

        return CommandResult()

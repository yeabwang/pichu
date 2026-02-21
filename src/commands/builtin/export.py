"""Export conversation to file."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class ExportCommand(SlashCommand):
    name = "export"
    description = "Export conversation to file"
    usage = "/export [filename]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        if not session or not session.context_manager:
            return CommandResult(error="No active session.")

        from pathlib import Path

        cm = session.context_manager
        messages = cm.get_messages()

        if not messages:
            return CommandResult(error="No conversation to export.")

        # Determine filename
        filename = args.strip() if args.strip() else None
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"conversation_{timestamp}.json"

        filepath = Path(filename)

        # Determine format from extension
        if filepath.suffix in (".md", ".markdown"):
            content = self._export_markdown(messages, session, config)
        else:
            # Default to JSON
            if not filepath.suffix:
                filepath = filepath.with_suffix(".json")
            content = self._export_json(messages, session, config)

        try:
            filepath.write_text(content, encoding="utf-8")
            return CommandResult(
                display=CommandDisplayPayload(
                    renderables=[
                        "",
                        f"  [success]✓[/success] Conversation exported to [path]{filepath}[/path]",
                        f"  [dim]{len(messages)} messages · {len(content):,} bytes[/dim]",
                        "",
                    ]
                )
            )
        except Exception as e:
            return CommandResult(error=f"Failed to write export: {e}")

    def _export_json(self, messages: list, session: "Session", config: "Config") -> str:
        import json

        total_usage = session.context_manager.get_total_usage() if session.context_manager else None
        export = {
            "session_id": session.session_id,
            "model": config.model,
            "created_at": session.created_at.isoformat(),
            "exported_at": datetime.now().isoformat(),
            "turns": session.turn_count,
            "messages": messages,
            "token_usage": {
                "prompt_tokens": total_usage.prompt_tokens if total_usage else 0,
                "completion_tokens": total_usage.completion_tokens if total_usage else 0,
                "total_tokens": total_usage.total_tokens if total_usage else 0,
            },
        }
        return json.dumps(export, indent=2, default=str)

    def _export_markdown(self, messages: list, session: "Session", config: "Config") -> str:
        lines = [
            "# Conversation Export",
            "",
            f"- **Session:** {session.session_id[:16]}",
            f"- **Model:** {config.model}",
            f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"- **Turns:** {session.turn_count}",
            "",
            "---",
            "",
        ]

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "system":
                lines.append("## System Prompt")
                lines.append("")
                lines.append(f"```\n{content[:500]}{'...' if len(content) > 500 else ''}\n```")
                lines.append("")
            elif role == "user":
                lines.append("## User")
                lines.append("")
                lines.append(content)
                lines.append("")
            elif role == "assistant":
                lines.append("## Assistant")
                lines.append("")
                lines.append(content if content else "*[tool calls only]*")
                lines.append("")

                # Include tool calls if any
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        lines.append(f"**Tool call:** `{func.get('name', 'unknown')}`")
                        lines.append("")
            elif role == "tool":
                tool_id = msg.get("tool_call_id", "")
                lines.append(f"### Tool Result ({tool_id[:8]})")
                lines.append("")
                truncated = content[:300] + "..." if len(content) > 300 else content
                lines.append(f"```\n{truncated}\n```")
                lines.append("")

        return "\n".join(lines)

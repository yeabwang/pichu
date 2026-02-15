"""Setup and inspect GitHub MCP integration."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand
from config.config import MCPServerConfig

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

_GITHUB_MCP_ARGS = [
    "run",
    "-i",
    "--rm",
    "-e",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "ghcr.io/github/github-mcp-server",
]
_GITHUB_REMOTE_URL = "https://api.githubcopilot.com/mcp/"


class GithubCommand(SlashCommand):
    name = "github"
    description = "Setup/status for GitHub MCP tasks"
    usage = "/github [status|setup [remote|readonly|local]|examples]"
    aliases = ["gh"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        tokens = args.strip().split()
        action = tokens[0].lower() if tokens else "status"

        if action == "status":
            return self._render_status(session, tui, config)
        if action in {"setup", "install"}:
            mode = tokens[1].lower() if len(tokens) > 1 else "remote"
            return await self._setup(session, tui, config, mode=mode)
        if action in {"examples", "tasks"}:
            return self._render_examples(tui)

        return CommandResult(error="Unknown /github action. Use /github [status|setup|examples].")

    def _render_status(self, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from rich import box
        from rich.panel import Panel
        from rich.table import Table

        snapshot = None
        configured_name = next(
            (name for name in config.mcp_servers.keys() if name.casefold() == "github"),
            None,
        )
        configured = configured_name is not None

        if session and session._mcp_manager:
            snapshots = session._mcp_manager.get_status_snapshot()
            snapshot = next(
                (candidate for candidate in snapshots if candidate.name.casefold() == "github"),
                None,
            )
            if snapshot is None:
                snapshot = next(
                    (candidate for candidate in snapshots if "github" in candidate.name.casefold()),
                    None,
                )

        server_config = config.mcp_servers.get(configured_name) if configured_name is not None else None

        if snapshot:
            raw_status = snapshot.status
        elif configured:
            raw_status = "configured"
        else:
            raw_status = "not_configured"

        if raw_status == "connected":
            status_text = "[success]✓ connected[/success]"
        elif raw_status == "disabled":
            status_text = "[dim]disabled[/dim]"
        elif raw_status == "configured":
            status_text = "[warning]configured (run /mcp reconnect)[/warning]"
        elif raw_status == "error":
            error_hint = f": {snapshot.error}" if snapshot and snapshot.error else ""
            status_text = f"[error]error{error_hint}[/error]"
        else:
            status_text = "[warning]not configured[/warning]"

        github_tools: list[str] = []
        if session:
            github_tools = [
                name for name in session.tool_registry.get_mcp_tool_names() if name.startswith("mcp__github__")
            ]

        profile = "unknown"
        if server_config is not None:
            if server_config.command:
                profile = "local (docker)"
            elif (server_config.url or "").rstrip("/").endswith("/readonly"):
                profile = "remote (read-only)"
            elif server_config.url:
                profile = "remote"

        table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
        table.add_column("Field", style="dim", min_width=18)
        table.add_column("Value", style="accent")
        table.add_row("Configured", "yes" if configured else "no")
        table.add_row("Profile", profile)
        table.add_row("Status", status_text)
        table.add_row("GitHub MCP tools", str(len(github_tools)))
        table.add_row(
            "Token env",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
        )

        tui.console.print()
        tui.console.print(
            Panel(
                table,
                title="[info]🐙 GitHub Integration[/info]",
                border_style="border",
                padding=(1, 1),
            )
        )
        tui.console.print("  [dim]Use /github setup to scaffold configuration.[/dim]")
        tui.console.print("  [dim]Use /github examples to see task prompts.[/dim]")
        tui.console.print()

        return CommandResult()

    @staticmethod
    def _normalize_mode(raw_mode: str) -> str | None:
        aliases = {
            "remote": "remote",
            "readonly": "readonly",
            "remote-readonly": "readonly",
            "remote_readonly": "readonly",
            "local": "local",
            "docker": "local",
        }
        return aliases.get(raw_mode.strip().lower())

    @staticmethod
    def _profile_payload(mode: str) -> tuple[dict[str, object], str]:
        if mode == "local":
            return (
                {
                    "command": "docker",
                    "args": list(_GITHUB_MCP_ARGS),
                    "startup_timeout_sec": 90.0,
                },
                "local Docker",
            )
        if mode == "readonly":
            return (
                {
                    "url": f"{_GITHUB_REMOTE_URL}readonly",
                    "transport": "http",
                    "startup_timeout_sec": 60.0,
                },
                "remote read-only",
            )
        return (
            {
                "url": _GITHUB_REMOTE_URL,
                "transport": "http",
                "startup_timeout_sec": 60.0,
            },
            "remote",
        )

    async def _setup(self, session: "Session", tui: "TUI", config: "Config", *, mode: str) -> CommandResult:
        from tomlkit import document, dumps, item, parse, table

        normalized_mode = self._normalize_mode(mode)
        if normalized_mode is None:
            return CommandResult(error="Unknown setup mode. Use /github setup [remote|readonly|local].")

        project_dir_name = os.environ.get("PICHU_PROJECT_DIR", ".pichu")
        config_file_name = os.environ.get("PICHU_CONFIG_FILE", "config.toml")

        config_dir = config.cwd / project_dir_name
        config_path = config_dir / config_file_name

        if config_path.exists():
            doc = parse(config_path.read_text(encoding="utf-8"))
        else:
            config_dir.mkdir(parents=True, exist_ok=True)
            doc = document()

        mcp_servers = doc.get("mcp_servers")
        if mcp_servers is None:
            mcp_servers = table()
            doc["mcp_servers"] = mcp_servers
        elif not isinstance(mcp_servers, MutableMapping):
            return CommandResult(error=f"Invalid config format: [mcp_servers] must be a table in {config_path}.")

        github_name = next(
            (name for name in mcp_servers.keys() if name.casefold() == "github"),
            "github",
        )
        payload, label = self._profile_payload(normalized_mode)
        mcp_servers[github_name] = item(payload)

        config_path.write_text(dumps(doc), encoding="utf-8")
        config.mcp_servers[github_name] = MCPServerConfig(**payload)  # type: ignore[arg-type]

        tui.console.print()
        tui.console.print(
            f"  [success]✓[/success] Configured [accent][mcp_servers.{github_name}][/accent] ({label}) in {config_path}"
        )
        if normalized_mode == "local":
            tui.console.print("  [dim]Set GITHUB_PERSONAL_ACCESS_TOKEN in your shell before reconnecting.[/dim]")
        else:
            tui.console.print("  [dim]Remote mode may require host OAuth or Authorization header configuration.[/dim]")

        if session and session._mcp_manager:
            try:
                tool_count = await session._mcp_manager.reconnect(session.tool_registry)
                tui.console.print(f"  [success]✓[/success] Reconnected MCP servers ({tool_count} tool(s) registered).")
            except Exception as exc:
                tui.console.print(f"  [warning]⚠[/warning] Config saved, but reconnect failed: {exc}")
                tui.console.print("  [dim]After setting token, run /mcp reconnect.[/dim]")

        tui.console.print()
        return CommandResult()

    def _render_examples(self, tui: "TUI") -> CommandResult:
        from rich.panel import Panel
        from rich.text import Text

        text = Text()
        text.append("Try prompts like:\n", style="bold")
        text.append("• List open pull requests for owner/repo and summarize risk.\n")
        text.append("• Show failed GitHub Actions runs in owner/repo.\n")
        text.append("• Read issue #123 and draft a fix plan.\n")
        text.append("• Search code for auth middleware in owner/repo.\n")
        text.append("• Compare latest commits on main and release branches.\n")

        tui.console.print()
        tui.console.print(
            Panel(
                text,
                title="[info]GitHub task examples[/info]",
                border_style="border",
                padding=(1, 1),
            )
        )
        tui.console.print()
        return CommandResult()

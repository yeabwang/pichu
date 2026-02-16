"""Setup and inspect GitHub MCP integration."""

from __future__ import annotations

import os
import shutil
from collections.abc import MutableMapping
from pathlib import Path
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
_GITHUB_TOKEN_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _find_env_file() -> Path:
    from config.loader import get_config_dir

    return get_config_dir() / ".env"


def _write_env_keys(env_path: Path, keys: dict[str, str]) -> None:
    lines: list[str] = []
    existing: dict[str, int] = {}

    if env_path.exists():
        raw = env_path.read_text(encoding="utf-8")
        lines = raw.splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                var_name = stripped.split("=", 1)[0].strip()
                existing[var_name] = i

    for var, value in keys.items():
        entry = f"{var}={value}\n"
        if var in existing:
            lines[existing[var]] = entry
        else:
            lines.append(entry)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("".join(lines), encoding="utf-8")


class GithubCommand(SlashCommand):
    name = "github"
    description = "Setup/status for GitHub MCP tasks"
    usage = "/github [status|setup [remote|readonly|local]|examples]"
    aliases = ["gh"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        tokens = args.strip().split()
        action = tokens[0].lower() if tokens else "status"

        if action == "status":
            configured_name = next(
                (name for name in config.mcp_servers.keys() if name.casefold() == "github"),
                None,
            )
            if configured_name is None:
                return await self._interactive_setup(session, tui, config, title="GitHub setup")
            return self._render_status(session, tui, config)
        if action in {"setup", "install"}:
            if len(tokens) > 1:
                mode = tokens[1].lower()
                return await self._setup(session, tui, config, mode=mode)
            return await self._interactive_setup(session, tui, config, title="Reconfigure GitHub MCP")
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
        elif raw_status == "connecting":
            status_text = "[warning]connecting[/warning]"
        elif raw_status == "disconnected":
            status_text = "[warning]disconnected (run /mcp reconnect)[/warning]"
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
            tool_prefix = "mcp__github__"
            if configured_name:
                safe_name = self._sanitize_identifier(configured_name)
                tool_prefix = f"mcp__{safe_name}__"
            github_tools = [name for name in session.tool_registry.get_mcp_tool_names() if name.startswith(tool_prefix)]

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
    def _sanitize_identifier(value: str) -> str:
        sanitized = "".join(char if char.isalnum() or char == "_" else "_" for char in value)
        sanitized = sanitized.strip("_")
        return sanitized or "unnamed"

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

    @staticmethod
    def _is_token_configured() -> bool:
        return bool(os.environ.get(_GITHUB_TOKEN_ENV, "").strip())

    def _check_token_on_status(self, tui: "TUI", config: "Config") -> None:
        return

    def _prompt_and_store_token(self, tui: "TUI") -> bool:
        token = os.environ.get(_GITHUB_TOKEN_ENV, "").strip()
        if token:
            return True

        tui.console.print()
        token = tui.console.input("  [bold]GitHub personal access token: [/bold]").strip()
        if not token:
            tui.console.print(f"  [warning]⚠[/warning] {_GITHUB_TOKEN_ENV} is required.")
            return False

        env_path = _find_env_file()
        _write_env_keys(env_path, {_GITHUB_TOKEN_ENV: token})
        os.environ[_GITHUB_TOKEN_ENV] = token

        tui.console.print(f"  [success]✓[/success] {_GITHUB_TOKEN_ENV} saved to {env_path}")
        tui.console.print(f"  [dim]Token:[/dim] {_mask_secret(token)}")
        return True

    async def _interactive_setup(
        self, session: "Session", tui: "TUI", config: "Config", *, title: str
    ) -> CommandResult:
        console = tui.console
        console.print()
        console.print(f"  [bold]{title}[/bold]")
        console.print("  [bold]Select a GitHub MCP profile:[/bold]")
        console.print()

        options: list[tuple[int, str]] = [
            (0, "Local (Docker)  [dim](requires docker + GITHUB_PERSONAL_ACCESS_TOKEN)[/dim]"),
            (1, "Remote  [dim](api.githubcopilot.com/mcp + GITHUB_PERSONAL_ACCESS_TOKEN)[/dim]"),
            (2, "Remote read-only  [dim](api.githubcopilot.com/mcp/readonly + token)[/dim]"),
        ]

        default_index = 1
        configured_name = next(
            (name for name in config.mcp_servers.keys() if name.casefold() == "github"),
            None,
        )
        if configured_name is not None:
            server_cfg = config.mcp_servers.get(configured_name)
            if server_cfg is not None:
                if server_cfg.command:
                    default_index = 0
                elif (server_cfg.url or "").rstrip("/").endswith("/readonly"):
                    default_index = 2
                elif server_cfg.url:
                    default_index = 1

        if hasattr(tui, "_interactive_select"):
            idx = tui._interactive_select(options, default_index=default_index)
        else:
            idx = default_index
        selected_mode = "local" if idx == 0 else "readonly" if idx == 2 else "remote"

        return await self._setup(session, tui, config, mode=selected_mode)

    def _print_mode_prereq(self, tui: "TUI", mode: str) -> None:
        if mode == "local" and shutil.which("docker") is None:
            tui.console.print("  [warning]⚠[/warning] Docker is not on PATH. Local GitHub MCP may fail to start.")
        if mode in {"remote", "readonly"}:
            tui.console.print(
                "  [dim]Remote profile uses api.githubcopilot.com and requires a valid GitHub personal access token.[/dim]"
            )

    async def _setup(self, session: "Session", tui: "TUI", config: "Config", *, mode: str) -> CommandResult:
        from tomlkit import document, dumps, item, parse, table

        normalized_mode = self._normalize_mode(mode)
        if normalized_mode is None:
            return CommandResult(error="Unknown setup mode. Use /github setup [remote|readonly|local].")

        self._print_mode_prereq(tui, normalized_mode)
        has_token = self._prompt_and_store_token(tui)
        if not has_token:
            return CommandResult(error=f"{_GITHUB_TOKEN_ENV} is required to configure GitHub MCP.")

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
                snapshots = session._mcp_manager.get_status_snapshot()
                github_snapshot = next(
                    (candidate for candidate in snapshots if candidate.name.casefold() == github_name.casefold()),
                    None,
                )
                if github_snapshot and github_snapshot.status == "connected":
                    tui.console.print(
                        f"  [success]✓[/success] Reconnected MCP servers ({tool_count} tool(s) registered)."
                    )
                elif github_snapshot and github_snapshot.status == "error":
                    tui.console.print(
                        f"  [warning]⚠[/warning] Reconnected MCP servers ({tool_count} tool(s) registered), "
                        f"but GitHub server is in error: {github_snapshot.error or 'unknown error'}."
                    )
                else:
                    state = github_snapshot.status if github_snapshot else "unknown"
                    tui.console.print(
                        f"  [warning]⚠[/warning] Reconnected MCP servers ({tool_count} tool(s) registered), "
                        f"but GitHub server status is {state}."
                    )
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

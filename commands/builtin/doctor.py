"""Health check for Pichu installation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class DoctorCommand(SlashCommand):
    name = "doctor"
    description = "Health check installation"
    usage = "/doctor"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        import sys
        from importlib.util import find_spec
        from pathlib import Path

        from rich.panel import Panel
        from rich.text import Text

        checks: list[tuple[str, bool, str]] = []

        # 1. Python version
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        py_ok = sys.version_info >= (3, 13)
        checks.append(
            (
                "Python version",
                py_ok,
                f"{py_ver}" + ("" if py_ok else " (3.13+ required)"),
            )
        )

        # 2. API key
        api_key_ok = bool(config.api_key)
        checks.append(
            (
                "API key configured",
                api_key_ok,
                "set" if api_key_ok else "LLM_API_KEY not set",
            )
        )

        # 3. Base URL
        base_url_ok = bool(config.base_url)
        checks.append(
            (
                "Base URL configured",
                base_url_ok,
                config.base_url[:50] if config.base_url else "LLM_BASE_URL not set",
            )
        )

        # 4. Config validation
        errors = config.validate()
        config_ok = len(errors) == 0
        checks.append(
            (
                "Config valid",
                config_ok,
                "OK" if config_ok else f"{len(errors)} error(s)",
            )
        )

        # 5. Working directory
        cwd_ok = config.cwd.exists() and config.cwd.is_dir()
        checks.append(("Working directory", cwd_ok, str(config.cwd)))

        # 6. Memory files
        agents_md = config.cwd / "AGENTS.md"
        checks.append(
            (
                "AGENTS.md",
                agents_md.exists(),
                str(agents_md) if agents_md.exists() else "not found (use /init)",
            )
        )

        # 7. Global config directory
        global_dir = Path.home() / ".pichu"
        checks.append(
            (
                "Global config dir",
                global_dir.exists(),
                str(global_dir) if global_dir.exists() else "not found",
            )
        )

        # 8. MCP servers
        if session and session._mcp_manager:
            snapshots = session._mcp_manager.get_status_snapshot()
            enabled = [snapshot for snapshot in snapshots if snapshot.enabled]
            if enabled:
                connected_count = sum(1 for snapshot in enabled if snapshot.status == "connected")
                checks.append(
                    (
                        "MCP servers",
                        connected_count == len(enabled),
                        f"{connected_count}/{len(enabled)} connected",
                    )
                )
            elif snapshots:
                checks.append(("MCP servers", True, "all configured servers disabled"))
            else:
                checks.append(("MCP servers", True, "none configured"))
        else:
            checks.append(("MCP servers", True, "none configured"))

        # 9. API connectivity
        api_reachable = False
        api_msg = "not tested"
        if config.base_url and config.api_key:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=5.0) as http_client:
                    base = config.base_url.rstrip("/")
                    resp = await http_client.get(
                        f"{base}/models",
                        headers={"Authorization": f"Bearer {config.api_key}"},
                    )
                    api_reachable = resp.status_code < 500
                    api_msg = f"HTTP {resp.status_code}"
            except Exception as e:
                api_msg = str(e)[:50]
        checks.append(("API connectivity", api_reachable, api_msg))

        # 10. Required packages
        required_packages = [
            "openai",
            "rich",
            "click",
            "tomlkit",
            "pydantic",
            "dotenv",
            "platformdirs",
            "tenacity",
            "pathspec",
        ]
        missing = []
        for pkg in required_packages:
            if find_spec(pkg) is None:
                missing.append(pkg)
        checks.append(
            (
                "Dependencies",
                len(missing) == 0,
                "OK" if not missing else f"Missing: {', '.join(missing)}",
            )
        )

        # Render
        lines = Text()
        pass_count = sum(1 for _, ok, _ in checks if ok)
        total_count = len(checks)

        for label, ok, detail in checks:
            icon = "[success]  ✓ [/success]" if ok else "[error]  ✗ [/error]"
            lines.append_text(Text.from_markup(f"{icon}"))
            lines.append(f"{label}: ", style="dim")
            style = "accent" if ok else "error"
            lines.append(f"{detail}\n", style=style)

        panel = Panel(
            lines,
            title=f"[info]🩺 Health Check ({pass_count}/{total_count} passed)[/info]",
            border_style="success" if pass_count == total_count else "warning",
            padding=(1, 1),
        )

        tui.console.print()
        tui.console.print(panel)

        if errors:
            tui.console.print("  [error]Config errors:[/error]")
            for err in errors:
                tui.console.print(f"    [error]• {err}[/error]")

        tui.console.print()

        return CommandResult()

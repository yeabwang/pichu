"""Interactive login — configure API key, base URL, and model."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


_PROVIDERS = [
    {
        "key": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "mistralai/devstral-2512:free",
    },
    {
        "key": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    {
        "key": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
    },
    {
        "key": "custom",
        "name": "Custom provider",
        "base_url": "",
        "default_model": "",
    },
]


def _mask_key(key: str) -> str:
    if len(key) > 8:
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"
    return "*" * len(key)


def _find_env_file() -> Path:
    """Return the .env file path in the system config directory."""
    from config.loader import get_config_dir

    return get_config_dir() / ".env"


def _write_env_keys(env_path: Path, keys: dict[str, str]) -> None:
    """Write or update key=value pairs in a .env file."""
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


class LoginCommand(SlashCommand):
    name = "login"
    description = "Configure API key, provider, and model"
    usage = "/login"
    aliases = ["auth"]

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        from config.loader import get_config_dir

        console = tui.console
        console.print()

        # ── 1. Choose provider (arrow-key selector) ─────────────
        console.print("  [bold]Select a provider:[/bold]")
        console.print()

        options: list[tuple[int, str]] = []
        for i, p in enumerate(_PROVIDERS):
            label = p["name"]
            if p["base_url"]:
                label += f"  [dim]({p['base_url']})[/dim]"
            options.append((i, label))

        idx = tui._interactive_select(options, default_index=0)
        provider = _PROVIDERS[idx]

        # ── 2. Base URL (only ask for custom) ───────────────────
        if provider["key"] == "custom":
            base_url = console.input("\n  [bold]Base URL: [/bold]").strip()
            if not base_url:
                return CommandResult(error="Base URL is required for custom providers.")
        else:
            base_url = provider["base_url"]

        # ── 3. API key ──────────────────────────────────────────
        api_key = console.input("\n  [bold]API key: [/bold]").strip()
        if not api_key:
            return CommandResult(error="API key is required.")

        # ── 4. Model ────────────────────────────────────────────
        default_model = provider["default_model"]
        if default_model:
            model = console.input(f"  [bold]Model [{default_model}]: [/bold]").strip() or default_model
        else:
            model = console.input("  [bold]Model: [/bold]").strip()
            if not model:
                return CommandResult(error="Model is required for custom providers.")

        # ── 5. Optional Serper API key for web search (skip with Enter) ────────
        serper_key = console.input(
            "  [bold]Serper API key [dim](optional, used for web search, Enter to skip)[/dim]: [/bold]"
        ).strip()

        # ── 6. Save API keys → .env ────────────────────────────
        env_path = _find_env_file()
        env_keys: dict[str, str] = {"LLM_API_KEY": api_key}
        if serper_key:
            env_keys["SERPER_API_KEY"] = serper_key

        _write_env_keys(env_path, env_keys)

        # ── 7. Save base_url + model → config.toml ─────────────
        config_dir = get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.toml"

        if config_file.exists():
            self._update_toml(config_file, base_url, model)
        else:
            self._write_fresh_toml(config_file, base_url, model)

        # ── 8. Apply to running session ─────────────────────────
        os.environ["LLM_API_KEY"] = api_key
        config.llm.api_key = api_key
        config.llm.base_url = base_url
        config.llm.model = model
        if serper_key:
            os.environ["SERPER_API_KEY"] = serper_key

        console.print()
        console.print(f"  [success]✓[/success] API keys saved to {env_path}")
        console.print(f"  [success]✓[/success] Config saved to {config_file}")
        console.print()
        console.print(f"  [dim]Provider:[/dim]    {provider['name']}")
        console.print(f"  [dim]Base URL:[/dim]    {base_url}")
        console.print(f"  [dim]Model:[/dim]      {model}")
        console.print(f"  [dim]LLM key:[/dim]    {_mask_key(api_key)}")
        if serper_key:
            console.print(f"  [dim]Serper key:[/dim]  {_mask_key(serper_key)}")
        else:
            console.print("  [dim]Serper key:[/dim]  [dim]skipped[/dim]")
        console.print()

        return CommandResult()

    @staticmethod
    def _update_toml(path: Path, base_url: str, model: str) -> None:
        """Update existing TOML config, preserving other settings."""
        from tomlkit import dumps as toml_dumps
        from tomlkit import load as toml_load

        with open(path, "r", encoding="utf-8") as f:
            doc = toml_load(f)

        if "llm" not in doc:
            doc["llm"] = {}

        llm_section = doc["llm"]
        llm_section["base_url"] = base_url  # type: ignore[index]
        llm_section["model"] = model  # type: ignore[index]

        # Remove api_key from config.toml if present (moved to .env)
        if "api_key" in llm_section:  # type: ignore[operator]
            del llm_section["api_key"]  # type: ignore[union-attr]

        path.write_text(toml_dumps(doc), encoding="utf-8")

    @staticmethod
    def _write_fresh_toml(path: Path, base_url: str, model: str) -> None:
        """Write a minimal config.toml with provider settings (no secrets)."""
        content = (
            "# Pichu Configuration\n"
            "# Run /login again to update, or edit this file directly.\n"
            "# API keys are stored in .env (same directory).\n\n"
            "[llm]\n"
            f'base_url = "{base_url}"\n'
            f'model = "{model}"\n'
        )
        path.write_text(content, encoding="utf-8")

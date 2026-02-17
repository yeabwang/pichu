"""Manage web cache scaffolding and status."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand
from commands.builtin._scaffold import relative_to_project
from utils.cache import WebCache

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


class CacheCommand(SlashCommand):
    name = "cache"
    description = "Manage cache storage"
    usage = "/cache [init|status]"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        action = args.strip().lower() if args.strip() else "status"

        if action == "init":
            return await self._init_cache(tui, config)

        return await self._status_cache(tui, config)

    async def _init_cache(self, tui: "TUI", config: "Config") -> CommandResult:
        cache_dir, search_ttl, fetch_ttl, max_size_mb = _cache_settings(config)
        dir_existed = cache_dir.exists()
        db_path = cache_dir / "web_cache.db"
        db_existed = db_path.exists()

        cache = WebCache(
            cache_dir=cache_dir,
            search_ttl_hours=search_ttl,
            fetch_ttl_hours=fetch_ttl,
            max_size_mb=max_size_mb,
        )
        cache.close()

        created: list[str] = []
        if not dir_existed:
            created.append(f"{relative_to_project(cache_dir, config.cwd)}/")
        if not db_existed:
            created.append(relative_to_project(db_path, config.cwd))

        tui.console.print()
        if created:
            for item in created:
                tui.console.print(f"  [success]✓[/success] Created {item}")
        else:
            tui.console.print("  [dim]Cache scaffolding already initialized.[/dim]")
        tui.console.print()
        return CommandResult()

    async def _status_cache(self, tui: "TUI", config: "Config") -> CommandResult:
        cache_dir, *_ = _cache_settings(config)
        db_path = cache_dir / "web_cache.db"

        tui.console.print()
        tui.console.print(f"  [dim]Cache directory:[/dim] {cache_dir}")
        tui.console.print(f"  [dim]Database:[/dim] {'present' if db_path.exists() else 'not initialized'}")
        tui.console.print("  [dim]Run /cache init to scaffold cache storage.[/dim]")
        tui.console.print()
        return CommandResult()


def _cache_settings(config: "Config") -> tuple[Path, float, float, float]:
    web_search_cfg = getattr(config, "web_search", None)
    cache_cfg = getattr(web_search_cfg, "cache", None)

    cache_dir_raw = getattr(cache_cfg, "cache_dir", ".pichu/cache")
    search_ttl = float(getattr(cache_cfg, "search_ttl_hours", 24.0))
    fetch_ttl = float(getattr(cache_cfg, "fetch_ttl_hours", 168.0))
    max_size_mb = float(getattr(cache_cfg, "max_size_mb", 100.0))

    cache_dir = Path(cache_dir_raw)
    if not cache_dir.is_absolute():
        cache_dir = config.cwd / cache_dir

    return cache_dir, search_ttl, fetch_ttl, max_size_mb

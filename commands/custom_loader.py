"""Load markdown-defined custom slash commands.

Search order:
- global: `~/.pichu/commands/*.md`
- project: `.pichu/commands/*.md` (overrides global custom commands)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from commands.base import CommandRegistry, CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI

logger = logging.getLogger(__name__)


class CustomSlashCommand(SlashCommand):
    """A user-defined slash command loaded from a markdown file."""

    def __init__(
        self,
        name: str,
        description: str,
        body: str,
        aliases: list[str] | None = None,
        source_path: Path | None = None,
    ) -> None:
        self.name = name.strip().lstrip("/").lower()
        self.description = description
        self.usage = f"/{self.name} [args]"
        self.aliases = [a.strip().lstrip("/").lower() for a in (aliases or []) if a]
        self.hidden = False
        self._body = body
        self._source_path = source_path

    def _substitute(self, body: str, args: str, config: "Config") -> str:
        """Apply substitution variables to the body text."""
        # Positional args
        parts = args.split() if args else []
        result = body.replace("$ARGUMENTS", args)
        for i, part in enumerate(parts, 1):
            result = result.replace(f"${i}", part)

        # Environment substitutions
        result = result.replace("$CWD", str(config.cwd))
        result = result.replace("$PROJECT", Path(config.cwd).name)
        result = result.replace("$MODEL", config.llm.model)

        return result

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        body = self._substitute(self._body, args, config)

        # Prefix modifier: ! → shell execution
        if body.lstrip().startswith("!"):
            command = body.lstrip()[1:].strip()
            tui.console.print(f"[dim]Running: {command}[/dim]")
            import asyncio
            import subprocess

            try:
                loop = asyncio.get_running_loop()
                proc = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        cwd=str(config.cwd),
                        timeout=60,
                    ),
                )
                output = proc.stdout
                if proc.stderr:
                    output += f"\n[stderr]\n{proc.stderr}"
                if proc.returncode != 0:
                    output += f"\n[exit code: {proc.returncode}]"
                tui.console.print(output)
                return CommandResult()
            except subprocess.TimeoutExpired:
                return CommandResult(error="Command timed out after 60s.")
            except Exception as e:
                return CommandResult(error=f"Shell execution failed: {e}")

        # Prefix modifier: @ → file injection
        if body.lstrip().startswith("@"):
            file_path = body.lstrip()[1:].strip().split("\n")[0].strip()
            rest = "\n".join(body.lstrip()[1:].strip().split("\n")[1:])
            try:
                resolved = Path(config.cwd) / file_path
                content = resolved.read_text(encoding="utf-8")
                prompt = f"Here is the contents of {file_path}:\n\n```\n{content}\n```\n\n{rest}"
                return CommandResult(inject_prompt=prompt)
            except FileNotFoundError:
                return CommandResult(error=f"File not found: {file_path}")
            except Exception as e:
                return CommandResult(error=f"Failed to read {file_path}: {e}")

        # Default: inject body as a prompt to the LLM
        return CommandResult(inject_prompt=body)


def _parse_custom_command(path: Path) -> CustomSlashCommand | None:
    """Parse a custom command from a markdown file with YAML frontmatter."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read custom command {path}: {e}")
        return None

    # Parse YAML frontmatter (--- ... ---)
    match = re.match(r"^---\s*\n(.+?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        logger.warning(f"No YAML frontmatter in {path}")
        return None

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as e:
        logger.warning(f"Invalid YAML frontmatter in {path}: {e}")
        return None

    if not isinstance(meta, dict):
        logger.warning(f"Invalid frontmatter format in {path}")
        return None

    name = str(meta.get("name", path.stem)).strip().lstrip("/").lower()
    if not name:
        logger.warning(f"Invalid command name in {path}")
        return None
    description = meta.get("description", f"Custom command: {name}")
    aliases = meta.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    body = match.group(2).strip()

    return CustomSlashCommand(
        name=name,
        description=description,
        body=body,
        aliases=aliases,
        source_path=path,
    )


def load_custom_commands(
    registry: CommandRegistry,
    project_root: Path | None = None,
) -> int:
    """Load custom commands from project and global directories.

    Returns the number of commands loaded.
    """
    count = 0
    load_sources: list[tuple[Path, bool]] = []

    # Global: ~/.pichu/commands/
    global_dir = Path.home() / ".pichu" / "commands"
    if global_dir.is_dir():
        load_sources.append((global_dir, False))

    # Project: .pichu/commands/
    if project_root:
        project_dir = project_root / ".pichu" / "commands"
        if project_dir.is_dir():
            load_sources.append((project_dir, True))

    # Process in order: global first, then project (project overrides global customs).
    for d, allow_override in load_sources:
        for md_file in sorted(d.glob("*.md")):
            cmd = _parse_custom_command(md_file)
            if not cmd:
                continue

            existing = registry.get(cmd.name)
            if existing and not isinstance(existing, CustomSlashCommand):
                logger.debug(f"Skipping custom command '{cmd.name}' — builtin exists")
                continue

            try:
                registry.register(
                    cmd,
                    replace=allow_override and isinstance(existing, CustomSlashCommand),
                )
            except ValueError as error:
                logger.warning("Skipping custom command '%s': %s", cmd.name, error)
                continue

            count += 1
            logger.debug("Loaded custom command: /%s from %s", cmd.name, md_file)

    return count

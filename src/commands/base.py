"""Base classes for the slash command system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


@dataclass
class CommandDisplayPayload:
    """Structured display payload rendered by the UI layer."""

    renderables: list[Any] = field(default_factory=list)


@dataclass
class CommandResult:
    """Result returned by a slash command execution."""

    output: str | None = None
    should_exit: bool = False
    should_clear: bool = False
    inject_prompt: str | None = None  # Send as new prompt to LLM
    error: str | None = None
    display: CommandDisplayPayload | None = None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Normalized metadata for a registered command."""

    name: str
    description: str
    usage: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    hidden: bool = False


class SlashCommand(ABC):
    """Abstract base class for slash commands."""

    name: str = ""
    description: str = ""
    usage: str = ""
    aliases: list[str] = []
    hidden: bool = False  # Hide from /help listing

    def spec(self) -> CommandSpec:
        """Return a normalized metadata view for this command."""
        name = self.name.strip().lstrip("/").lower()
        aliases = tuple(alias.strip().lstrip("/").lower() for alias in self.aliases if alias.strip())
        usage = self.usage.strip() if self.usage.strip() else f"/{name}"
        return CommandSpec(
            name=name,
            description=self.description.strip(),
            usage=usage,
            aliases=aliases,
            hidden=self.hidden,
        )

    @abstractmethod
    async def execute(
        self,
        args: str,
        session: "Session",
        tui: "TUI",
        config: "Config",
    ) -> CommandResult:
        """Execute the command.

        Args:
            args: Everything after the command name (stripped).
            session: The current agent session.
            tui: The TUI instance for rendering output.
            config: Application configuration.

        Returns:
            CommandResult describing what the caller should do next.
        """
        ...


class CommandRegistry:
    """Registry of all available slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    def _remove_aliases_for(self, canonical_name: str) -> None:
        self._aliases = {alias: resolved for alias, resolved in self._aliases.items() if resolved != canonical_name}

    def register(self, command: SlashCommand, *, replace: bool = False) -> None:
        """Register a slash command with validation and collision checks."""
        spec = command.spec()
        if not spec.name:
            raise ValueError("Command name cannot be empty.")

        existing = self._commands.get(spec.name)
        if existing and not replace:
            raise ValueError(f"Command '/{spec.name}' is already registered.")

        if existing and replace:
            self._remove_aliases_for(spec.name)

        for alias in spec.aliases:
            if alias == spec.name:
                continue
            if alias in self._commands and alias != spec.name:
                raise ValueError(f"Alias '{alias}' conflicts with existing command '/{alias}'.")
            alias_target = self._aliases.get(alias)
            if alias_target and alias_target != spec.name:
                raise ValueError(f"Alias '{alias}' is already used by '/{alias_target}'.")

        self._commands[spec.name] = command
        setattr(command, "_registry", self)
        for alias in spec.aliases:
            if alias != spec.name:
                self._aliases[alias] = spec.name

    def get(self, name: str) -> SlashCommand | None:
        """Get a command by name or alias."""
        if name in self._commands:
            return self._commands[name]
        canonical = self._aliases.get(name)
        if canonical:
            return self._commands.get(canonical)
        return None

    def resolve_name(self, name: str) -> str | None:
        """Resolve command input to canonical command name."""
        normalized = name.strip().lstrip("/").lower()
        if normalized in self._commands:
            return normalized
        return self._aliases.get(normalized)

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        """Return best-effort command suggestions for unknown commands."""
        normalized = name.strip().lstrip("/").lower()
        if not normalized:
            return []

        candidates = sorted(set(self._commands.keys()) | set(self._aliases.keys()))
        matches = get_close_matches(normalized, candidates, n=limit * 2, cutoff=0.4)
        resolved: list[str] = []
        for match in matches:
            canonical = self.resolve_name(match)
            if canonical and canonical not in resolved:
                resolved.append(canonical)
            if len(resolved) >= limit:
                break
        return resolved

    def list_all(self) -> list[SlashCommand]:
        """Return all registered commands (excluding hidden ones)."""
        return [c for c in self._commands.values() if not c.spec().hidden]

    def list_all_including_hidden(self) -> list[SlashCommand]:
        """Return all registered commands including hidden ones."""
        return list(self._commands.values())

    def specs(self, include_hidden: bool = False) -> list[CommandSpec]:
        """Return command metadata for help/autocomplete surfaces."""
        commands = self.list_all_including_hidden() if include_hidden else self.list_all()
        return [command.spec() for command in commands]

    def names(self) -> list[str]:
        """Return sorted list of command names."""
        return sorted(self._commands.keys())

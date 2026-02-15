"""Slash command system for Pichu interactive mode."""

from pathlib import Path

from commands.base import CommandRegistry, CommandResult, SlashCommand
from commands.builtin import register_all_commands
from commands.custom_loader import CustomSlashCommand, load_custom_commands
from commands.router import CommandRouter


def create_command_router(
    project_root: Path | None = None,
) -> tuple[CommandRouter, CommandRegistry]:
    """Build router + registry with builtin and custom commands loaded."""
    registry = CommandRegistry()
    register_all_commands(registry)
    load_custom_commands(registry, project_root=project_root)
    return CommandRouter(registry), registry


__all__ = [
    "SlashCommand",
    "CommandResult",
    "CommandRegistry",
    "CommandRouter",
    "CustomSlashCommand",
    "load_custom_commands",
    "create_command_router",
]

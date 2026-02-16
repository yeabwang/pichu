"""Builtin slash commands registration."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from functools import lru_cache

from commands.base import CommandRegistry, SlashCommand


@lru_cache(maxsize=1)
def _discover_builtin_command_types() -> tuple[type[SlashCommand], ...]:
    """Discover all SlashCommand subclasses from builtin command modules."""
    command_types: list[type[SlashCommand]] = []
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if module_info.name.startswith("_"):
            continue

        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is SlashCommand or not issubclass(cls, SlashCommand):
                continue
            if cls.__module__ != module.__name__:
                continue
            command_types.append(cls)

    return tuple(sorted(command_types, key=lambda cls: cls.__name__))


BUILTIN_COMMANDS: tuple[type[SlashCommand], ...] = _discover_builtin_command_types()


def build_builtin_commands() -> list[SlashCommand]:
    """Create builtin command instances in their registration order."""
    return [command_type() for command_type in BUILTIN_COMMANDS]


def register_all_commands(registry: CommandRegistry) -> None:
    """Register all builtin slash commands."""
    for command in build_builtin_commands():
        registry.register(command)

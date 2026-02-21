from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from commands.base import CommandDisplayPayload


@dataclass(frozen=True, slots=True)
class UIEvent:
    """Generic UI event contract for controller/renderer boundaries."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandChoice:
    name: str
    description: str = ""
    aliases: tuple[str, ...] = ()


__all__ = ["UIEvent", "CommandChoice", "CommandDisplayPayload"]

"""Regression tests for commands module architecture and contracts."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from commands.base import CommandRegistry, CommandResult, SlashCommand
from commands.builtin import (
    BUILTIN_COMMANDS,
    build_builtin_commands,
    register_all_commands,
)
from commands.builtin.help import HelpCommand
from commands.builtin.init import InitCommand
from commands.custom_loader import CustomSlashCommand, load_custom_commands
from commands.router import CommandRouter


class _DummyCommand(SlashCommand):
    def __init__(
        self,
        name: str,
        description: str = "dummy command",
        usage: str | None = None,
        aliases: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.usage = usage or f"/{name}"
        self.aliases = aliases or []
        self.hidden = False

    async def execute(self, args, session, tui, config) -> CommandResult:
        return CommandResult(output=f"ran:{self.name}:{args}")


@dataclass
class _DummyLLM:
    model: str = "test-model"


@dataclass
class _DummyConfig:
    cwd: Path
    llm: _DummyLLM = field(default_factory=_DummyLLM)


class _DummyTUI:
    console = None


class _NoopConsole:
    def print(self, *args, **kwargs) -> None:
        return None


class _ConsoleTUI:
    def __init__(self) -> None:
        self.console = _NoopConsole()


def _write_custom_command(path: Path, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} command\naliases: []\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_registry_rejects_duplicate_command_names():
    registry = CommandRegistry()
    registry.register(_DummyCommand("alpha"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_DummyCommand("alpha"))


def test_registry_detects_alias_collisions():
    registry = CommandRegistry()
    registry.register(_DummyCommand("alpha", aliases=["a"]))

    with pytest.raises(ValueError, match="Alias 'a'"):
        registry.register(_DummyCommand("beta", aliases=["a"]))


@pytest.mark.asyncio
async def test_router_unknown_command_includes_suggestions():
    registry = CommandRegistry()
    registry.register(_DummyCommand("status"))
    router = CommandRouter(registry)

    result = await router.dispatch("/statu", session=object(), tui=object(), config=object())

    assert result.error is not None
    assert "Did you mean: /status?" in result.error


@pytest.mark.asyncio
async def test_help_command_uses_registry_metadata_for_command_details():
    registry = CommandRegistry()
    registry.register(HelpCommand())
    registry.register(
        _DummyCommand(
            "alpha",
            description="Alpha command",
            usage="/alpha [value]",
            aliases=["a"],
        )
    )

    command = registry.get("help")
    assert command is not None

    result = await command.execute("alpha", session=object(), tui=_DummyTUI(), config=object())

    assert result.output is not None
    assert "/alpha" in result.output
    assert "Alpha command" in result.output
    assert "/a" in result.output


@pytest.mark.asyncio
async def test_project_custom_commands_override_global_custom_commands(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = tmp_path / "project"
    global_file = home_dir / ".pichu" / "commands" / "deploy.md"
    project_file = project_root / ".pichu" / "commands" / "deploy.md"

    _write_custom_command(global_file, "deploy", "global body")
    _write_custom_command(project_file, "deploy", "project body")

    import commands.custom_loader as custom_loader_module

    monkeypatch.setattr(
        custom_loader_module.Path,
        "home",
        classmethod(lambda cls: home_dir),
    )

    registry = CommandRegistry()
    loaded = load_custom_commands(registry, project_root=project_root)

    assert loaded == 2
    command = registry.get("deploy")
    assert isinstance(command, CustomSlashCommand)

    result = await command.execute(
        "",
        session=object(),
        tui=_DummyTUI(),
        config=_DummyConfig(cwd=project_root),
    )
    assert result.inject_prompt == "project body"


def test_builtin_registry_includes_all_builtin_command_classes():
    builtin_dir = Path(__file__).parent.parent / "commands" / "builtin"
    discovered: set[str] = set()
    for file_path in builtin_dir.glob("*.py"):
        if file_path.name == "__init__.py":
            continue
        module = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "SlashCommand":
                    discovered.add(node.name)
                if isinstance(base, ast.Attribute) and base.attr == "SlashCommand":
                    discovered.add(node.name)

    registered = {command.__name__ for command in BUILTIN_COMMANDS}
    assert discovered == registered


@pytest.mark.asyncio
async def test_help_lists_all_registered_builtin_commands():
    registry = CommandRegistry()
    register_all_commands(registry)

    help_command = registry.get("help")
    assert isinstance(help_command, HelpCommand)

    captured: dict[str, list[str]] = {}
    original_specs = registry.specs

    def _capture_specs(include_hidden: bool = False):
        specs = original_specs(include_hidden=include_hidden)
        captured["usages"] = [spec.usage for spec in specs]
        return specs

    registry.specs = _capture_specs  # type: ignore[method-assign]

    tui = _ConsoleTUI()
    result = await help_command.execute("", session=object(), tui=tui, config=object())
    assert result.error is None

    expected = {command.spec().usage for command in build_builtin_commands()}
    assert set(captured.get("usages", [])) == expected


@pytest.mark.asyncio
async def test_init_writes_full_project_config_without_login_managed_model_settings(tmp_path):
    command = InitCommand()
    result = await command.execute("", session=object(), tui=_ConsoleTUI(), config=_DummyConfig(cwd=tmp_path))
    assert result.error is None

    config_path = tmp_path / ".PICHU" / "config.toml"
    assert config_path.exists()

    config_text = config_path.read_text(encoding="utf-8")
    assert "[limits]" in config_text
    assert "[web_search]" in config_text
    assert "[web_fetch]" in config_text
    assert 'base_url = "' not in config_text
    assert 'model = "' not in config_text

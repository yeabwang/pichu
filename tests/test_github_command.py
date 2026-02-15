"""Tests for /github command setup behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from commands.builtin.github import GithubCommand
from config.config import Config


class _NoopConsole:
    def print(self, *args, **kwargs) -> None:
        return None


class _TUI:
    def __init__(self) -> None:
        self.console = _NoopConsole()


@pytest.mark.asyncio
async def test_github_setup_creates_mcp_server_config(tmp_path, monkeypatch):
    from tomlkit import parse

    monkeypatch.setenv("PICHU_PROJECT_DIR", ".PICHU")
    monkeypatch.setenv("PICHU_CONFIG_FILE", "config.toml")

    command = GithubCommand()
    config = Config(cwd=tmp_path)
    tui = _TUI()

    result = await command.execute("setup", session=None, tui=tui, config=config)

    assert result.error is None

    config_path = tmp_path / ".PICHU" / "config.toml"
    assert config_path.exists()

    parsed = parse(config_path.read_text(encoding="utf-8"))
    github = parsed["mcp_servers"]["github"]
    assert github["url"] == "https://api.githubcopilot.com/mcp/"
    assert github["transport"] == "http"
    assert "github" in config.mcp_servers


@pytest.mark.asyncio
async def test_github_setup_is_idempotent(tmp_path, monkeypatch):
    from tomlkit import parse

    monkeypatch.setenv("PICHU_PROJECT_DIR", ".PICHU")
    monkeypatch.setenv("PICHU_CONFIG_FILE", "config.toml")

    command = GithubCommand()
    config = Config(cwd=tmp_path)
    tui = _TUI()

    first = await command.execute("setup", session=None, tui=tui, config=config)
    second = await command.execute("setup", session=None, tui=tui, config=config)

    assert first.error is None
    assert second.error is None

    parsed = parse((tmp_path / ".PICHU" / "config.toml").read_text(encoding="utf-8"))
    assert list(parsed["mcp_servers"].keys()).count("github") == 1


@pytest.mark.asyncio
async def test_github_setup_local_mode_creates_docker_profile(tmp_path, monkeypatch):
    from tomlkit import parse

    monkeypatch.setenv("PICHU_PROJECT_DIR", ".PICHU")
    monkeypatch.setenv("PICHU_CONFIG_FILE", "config.toml")

    command = GithubCommand()
    config = Config(cwd=tmp_path)
    tui = _TUI()

    result = await command.execute("setup local", session=None, tui=tui, config=config)
    assert result.error is None

    parsed = parse((tmp_path / ".PICHU" / "config.toml").read_text(encoding="utf-8"))
    github = parsed["mcp_servers"]["github"]
    assert github["command"] == "docker"
    assert list(github["args"]) == [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ghcr.io/github/github-mcp-server",
    ]

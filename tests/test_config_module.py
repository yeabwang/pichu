"""Regression tests for the config schema and loader pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config.loader as loader
from config import load_config, reset_config
from config.config import ApprovalPolicy, Config, MCPServerConfig


def test_update_from_dict_normalizes_hooks_and_mcp_servers():
    cfg = Config()

    cfg.update_from_dict(
        {
            "approval": {"policy": "auto_edit"},
            "hooks": {
                "disabled": True,
                "PreToolUse": {
                    "matcher": "shell",
                    "hooks": {"type": "command", "command": "echo ok", "timeout": 5},
                },
            },
            "mcp_servers": {
                "example": {
                    "url": "http://localhost:8080/mcp",
                    "transport": "http",
                    "headers": {"Authorization": "Bearer test"},
                    "enabled": True,
                }
            },
        }
    )

    assert cfg.approval.policy == ApprovalPolicy.AUTO_EDIT
    assert cfg.hooks.disabled is True
    assert len(cfg.hooks.PreToolUse) == 1
    assert cfg.hooks.PreToolUse[0].matcher == "shell"
    assert cfg.hooks.PreToolUse[0].hooks[0].command == "echo ok"
    assert isinstance(cfg.mcp_servers["example"], MCPServerConfig)
    assert cfg.mcp_servers["example"].url == "http://localhost:8080/mcp"
    assert cfg.mcp_servers["example"].transport == "http"
    assert cfg.mcp_servers["example"].headers["Authorization"] == "Bearer test"


def test_update_from_dict_rejects_invalid_mcp_server_payload():
    cfg = Config()
    with pytest.raises(TypeError, match="mcp_servers.bad"):
        cfg.update_from_dict({"mcp_servers": {"bad": "not-a-mapping"}})


def test_mcp_server_config_rejects_remote_fields_for_stdio():
    with pytest.raises(
        ValueError,
        match="'transport' can only be set when 'url' is configured for MCPServerConfig.",
    ):
        MCPServerConfig(command="python", transport="http")


def test_load_config_merges_system_project_and_env_sources(tmp_path, monkeypatch):
    reset_config()

    system_dir = tmp_path / "system-config"
    system_dir.mkdir(parents=True)
    (system_dir / "config.toml").write_text(
        ('[llm]\nmodel = "system-model"\nbase_url = "https://system.example"\n\n[limits]\ncontext_window = 100\n'),
        encoding="utf-8",
    )

    project_root = tmp_path / "repo"
    project_cfg_dir = project_root / ".PICHU"
    project_cfg_dir.mkdir(parents=True)
    (project_cfg_dir / "config.toml").write_text(
        ('[llm]\nmodel = "project-model"\n\n[limits]\ncontext_window = 200\n\n[approval]\npolicy = "auto_edit"\n'),
        encoding="utf-8",
    )
    (project_cfg_dir / "AGENT.md").write_text("Developer constraints.", encoding="utf-8")

    monkeypatch.setattr(loader, "get_config_dir", lambda: system_dir)
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("PICHU_DEBUG", "true")

    cfg = load_config(cwd=project_root / "src" / "nested")

    assert cfg.model == "project-model"
    assert cfg.base_url == "https://system.example"
    assert cfg.limits.context_window == 200
    assert cfg.api_key == "env-key"
    assert cfg.debug is True
    assert cfg.approval.policy == ApprovalPolicy.AUTO_EDIT
    assert cfg.developer_instructions == "Developer constraints."

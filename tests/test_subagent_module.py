"""Regression tests for subagent orchestration contracts."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config, LLMConfig
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.subagent import SubAgentTool
from tools.registry import ToolRegistry
from utils.subagent_loader import SubAgentLoader
from utils.subagent_types import SubAgentConfig, SubAgentResult


class _DummyTool(Tool):
    description = "dummy tool"

    def __init__(self, name: str, kind: ToolKind = ToolKind.READ) -> None:
        super().__init__(None)
        self.name = name
        self.kind = kind

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult.success_result(output=f"ok:{self.name}")


def test_subagent_loader_parses_max_turns_and_validates_name(tmp_path):
    loader = SubAgentLoader(project_root=tmp_path, user_agents_dir=tmp_path / "user")

    config = loader.parse_content(
        "---\n"
        "name: code-reviewer\n"
        "description: Reviews code quality\n"
        "maxTurns: 7\n"
        "---\n\n"
        "You are a review specialist.\n"
    )
    assert config is not None
    assert config.max_turns == 7

    invalid = loader.parse_content("---\nname: CodeReviewer\ndescription: Invalid name example\n---\n\nPrompt text.\n")
    assert invalid is None


def test_subagent_tool_filters_allowed_and_disallowed_tools(tmp_path):
    registry = ToolRegistry()
    registry.register_tool(_DummyTool("read_file", kind=ToolKind.READ))
    registry.register_tool(_DummyTool("shell-tool", kind=ToolKind.SHELL))
    registry.register_tool(_DummyTool("subagent_nested", kind=ToolKind.READ))

    cfg = Config()
    cfg.cwd = tmp_path
    llm_cfg = LLMConfig(model="test-model")
    subagent_cfg = SubAgentConfig(
        name="architect",
        description="Architecture specialist",
        system_prompt="You are an architect.",
        tools=["ReadFile", "shell_tool"],
        disallowed_tools=["SHELL-TOOL"],
    )
    tool = SubAgentTool(
        config=subagent_cfg,
        llm_config=llm_cfg,
        tool_registry=registry,
        main_config=cfg,
        session_id="test-session",
    )

    assert [t.name for t in tool.get_allowed_tools()] == ["read_file"]


def test_subagent_turn_limit_falls_back_to_default():
    cfg = SubAgentConfig(
        name="planner",
        description="planning agent",
        system_prompt="Plan work.",
        max_turns=0,
    )
    assert cfg.get_turn_limit(default_turn_limit=30) == 30

    cfg.max_turns = 12
    assert cfg.get_turn_limit(default_turn_limit=30) == 12


@pytest.mark.asyncio
async def test_background_task_status_cleans_up_completed_tasks():
    SubAgentTool._background_tasks.clear()

    async def _done() -> SubAgentResult:
        return SubAgentResult.from_success("done", turns=2, agent_id="agent-1")

    task = asyncio.create_task(_done())
    await task
    SubAgentTool._background_tasks["task-agent-1"] = task

    status = SubAgentTool.get_background_task_status("task-agent-1")
    assert status["status"] == "completed"
    assert status["agent_id"] == "agent-1"
    assert "task-agent-1" not in SubAgentTool._background_tasks

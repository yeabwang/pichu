"""Regression tests for tool registry selection behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from safety.approval import ApprovalDecision
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.registry import ToolRegistry


class _DummyTool(Tool):
    description = "dummy"

    def __init__(self, name: str, kind: ToolKind = ToolKind.READ) -> None:
        super().__init__(None)
        self.name = name
        self.kind = kind

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult.success_result(output="ok")


class _CountingApprovalManager:
    def __init__(self) -> None:
        self.calls = 0

    async def check_and_approve(self, confirmation) -> ApprovalDecision:
        self.calls += 1
        return ApprovalDecision.APPROVED


def test_select_tools_respects_allow_and_deny_with_normalized_names():
    registry = ToolRegistry()
    registry.register_tool(_DummyTool("read_file"))
    registry.register_tool(_DummyTool("shell-tool", kind=ToolKind.SHELL))
    registry.register_tool(_DummyTool("subagent_reviewer"))

    selected = registry.select_tools(
        allow=["ReadFile", "SHELL_TOOL", "subagent-*"],
        deny=["shelltool"],
        include_subagents=False,
    )

    assert [tool.name for tool in selected] == ["read_file"]


def test_select_tools_can_exclude_mcp_tools():
    registry = ToolRegistry()
    registry.register_tool(_DummyTool("read_file"))
    registry.register_mcp_tool(_DummyTool("mcp__github__search_code"))

    without_mcp = registry.select_tools(include_mcp=False)
    with_mcp = registry.select_tools(include_mcp=True)

    assert [tool.name for tool in without_mcp] == ["read_file"]
    assert [tool.name for tool in with_mcp] == [
        "read_file",
        "mcp__github__search_code",
    ]


@pytest.mark.asyncio
async def test_invoke_skips_approval_for_task_crud_tools():
    registry = ToolRegistry()
    registry.register_tool(_DummyTool("task_create", kind=ToolKind.MEMORY))
    approval_manager = _CountingApprovalManager()

    result = await registry.invoke(
        "task_create",
        params={},
        cwd=Path.cwd(),
        approval_manager=approval_manager,
    )

    assert result.success
    assert approval_manager.calls == 0


@pytest.mark.asyncio
async def test_invoke_still_checks_approval_for_other_mutating_tools():
    registry = ToolRegistry()
    registry.register_tool(_DummyTool("memory_write", kind=ToolKind.MEMORY))
    approval_manager = _CountingApprovalManager()

    result = await registry.invoke(
        "memory_write",
        params={},
        cwd=Path.cwd(),
        approval_manager=approval_manager,
    )

    assert result.success
    assert approval_manager.calls == 1

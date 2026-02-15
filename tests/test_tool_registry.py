"""Regression tests for tool registry selection behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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

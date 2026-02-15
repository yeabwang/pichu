"""Tests for MCP manager lifecycle and naming behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from tools.mcp.client import MCPServerStatus, MCPToolInfo
from tools.mcp.mcp_manager import MCPManager
from tools.registry import ToolRegistry


def test_register_tools_uses_namespaced_tool_names_and_rebuilds_registry():
    cfg = Config()
    manager = MCPManager(cfg)
    manager._clients = {
        "fs.server": SimpleNamespace(
            name="fs.server",
            status=MCPServerStatus.CONNECTED,
            tools=[
                MCPToolInfo(
                    name="create-file",
                    description="Create file",
                    server_name="fs.server",
                )
            ],
            last_error=None,
        )
    }

    registry = ToolRegistry()
    registry.register_mcp_tool(SimpleNamespace(name="stale_mcp_tool"))

    count = manager.register_tools(registry)

    assert count == 1
    assert registry.get_mcp_tool_names() == ["mcp__fs_server__create_file"]


def test_get_status_snapshot_includes_disabled_and_uninitialized_servers():
    cfg = Config()
    cfg.update_from_dict(
        {
            "mcp_servers": {
                "disabled": {"url": "http://localhost:8080/sse", "enabled": False},
                "broken": {
                    "url": "http://localhost:9000/mcp",
                    "enabled": True,
                    "transport": "http",
                },
                "pending": {"url": "http://localhost:9010/sse", "enabled": True},
            }
        }
    )

    manager = MCPManager(cfg)
    manager._clients = {
        "broken": SimpleNamespace(
            name="broken",
            status=MCPServerStatus.ERROR,
            tools=[],
            last_error="connection refused",
        )
    }

    snapshots = {snapshot.name: snapshot for snapshot in manager.get_status_snapshot()}

    assert snapshots["disabled"].status == "disabled"
    assert snapshots["disabled"].transport == "sse"
    assert snapshots["broken"].status == "error"
    assert snapshots["broken"].transport == "http"
    assert snapshots["broken"].error == "connection refused"
    assert snapshots["pending"].status == "not_initialized"

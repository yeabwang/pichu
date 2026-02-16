"""MCP (Model Context Protocol) integration for Pichu.

This module provides tools and clients for interacting with MCP servers,
enabling external tool integration through the Model Context Protocol.

Components:
- MCPManager: Manages MCP server connections and tool registration
- MCPClient: Handles communication with individual MCP servers
- MCPTool: Wraps MCP tools for use in the tool registry
- MCPServerStatus: Connection status enum
- MCPToolInfo: Tool metadata dataclass
"""

from tools.mcp.client import MCPClient, MCPServerStatus, MCPToolInfo
from tools.mcp.mcp_manager import MCPManager, MCPServerSnapshot
from tools.mcp.mcp_tool import MCPTool

__all__ = [
    "MCPManager",
    "MCPServerSnapshot",
    "MCPClient",
    "MCPServerStatus",
    "MCPToolInfo",
    "MCPTool",
]

from typing import Any

from config.config import Config

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from tools.mcp.client import MCPClient, MCPServerStatus, MCPToolInfo


class MCPTool(Tool):
    def __init__(
        self,
        config: Config,
        tool_info: MCPToolInfo,
        client: MCPClient,
        name: str | None = None,
    ) -> None:
        super().__init__(config=config)
        self._tool_info = tool_info
        self._client = client
        self.name: str = name or self._tool_info.name or "unknown_mcp_tool"
        self.description = self._tool_info.description or ""
        # Store schema in private attribute, expose via property
        input_schema = tool_info.input_schema or {}
        self._schema = {
            "type": "object",
            "properties": input_schema.get("properties", {}),
            "required": input_schema.get("required", []),
        }

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    def is_mutating(self, params: dict[str, Any]) -> bool:
        return True  # assume all MCP tools are mutating for now, can expand later based on tool_info or params

    kind: ToolKind = ToolKind.MCP

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"MCP tool '{self.name}' on server '{self._client.name}'",
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        if self._client.status != MCPServerStatus.CONNECTED:
            return ToolResult.error_result(
                error_message=f"MCP server '{self._client.name}' is not connected.",
            )
        try:
            result = await self._client.call_tools(
                tool_name=self._tool_info.name,
                arguments=invocation.params,
            )
            output = result.get("output", "") if isinstance(result, dict) else ""
            is_error = result.get("is_error", False) if isinstance(result, dict) else False
            if is_error:
                return ToolResult.error_result(
                    error_message=f"Error from MCP tool '{self.name}'",
                    output=output,
                )
            else:
                return ToolResult.success_result(output=output)
        except Exception as e:
            return ToolResult.error_result(
                error_message=f"Error executing MCP tool '{self.name}': {str(e)}",
            )

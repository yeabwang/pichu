import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)

from config.config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPServerStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class MCPToolInfo:
    name: str
    description: str
    server_name: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class MCPClient:
    def __init__(self, name: str, config: MCPServerConfig, cwd: Path | None = None) -> None:
        self.name = name
        self.config = config
        self.cwd = cwd or Path.cwd()
        self.status: MCPServerStatus = MCPServerStatus.DISCONNECTED
        self.last_error: str | None = None
        self._client: Client | None = None
        self._tools: dict[str, MCPToolInfo] = {}

    @property
    def tools(self) -> list[MCPToolInfo]:
        return list(self._tools.values())

    def _create_transports(self) -> list[tuple[str, Any]]:
        if self.config.command:
            env = os.environ.copy()
            env.update(self.config.env)
            # Use config cwd if set, otherwise use instance cwd
            cwd = str(self.config.cwd) if self.config.cwd else str(self.cwd)
            logger.debug(f"Creating StdioTransport: command={self.config.command}, args={self.config.args}, cwd={cwd}")
            return [
                (
                    "stdio",
                    StdioTransport(
                        command=self.config.command,
                        args=list(self.config.args),
                        env=env,
                        cwd=cwd,
                    ),
                )
            ]

        headers = dict(self.config.headers) if self.config.headers else None
        transport = self.config.transport or "sse"
        transports: list[tuple[str, Any]] = []
        if transport in {"http", "auto"} and self.config.url:
            logger.debug(f"Creating StreamableHttpTransport: url={self.config.url}")
            transports.append(
                (
                    "http",
                    StreamableHttpTransport(
                        url=self.config.url,
                        headers=headers,
                    ),
                )
            )
        if transport in {"sse", "auto"} and self.config.url:
            logger.debug(f"Creating SSETransport: url={self.config.url}")
            transports.append(
                (
                    "sse",
                    SSETransport(
                        url=self.config.url,
                        headers=headers,
                    ),
                )
            )
        return transports

    async def connect(self) -> None:
        if self.status == MCPServerStatus.CONNECTED:
            return

        # Reset previous state before attempting a new connection.
        self._tools.clear()
        self.last_error = None
        self.status = MCPServerStatus.CONNECTING
        logger.info(f"Connecting to MCP server '{self.name}'...")
        last_exception: Exception | None = None
        for transport_name, transport in self._create_transports():
            client: Client | None = None
            try:
                client = Client(transport=transport)
                await client.__aenter__()
                # list_tools() returns list[mcp.types.Tool] directly.
                tools = await client.list_tools()
                logger.info(f"MCP server '{self.name}' connected via {transport_name} with {len(tools)} tools")
                for tool in tools:
                    # mcp.types.Tool uses camelCase: inputSchema.
                    self._tools[tool.name] = MCPToolInfo(
                        name=tool.name,
                        description=tool.description or "",
                        server_name=self.name,
                        input_schema=getattr(tool, "inputSchema", {}) or {},
                    )
                self._client = client
                self.status = MCPServerStatus.CONNECTED
                self.last_error = None
                return
            except Exception as e:
                last_exception = e
                self.last_error = str(e)
                logger.warning(f"MCP server '{self.name}' failed via {transport_name} transport: {e}")
                if client:
                    await client.__aexit__(None, None, None)

        self.status = MCPServerStatus.ERROR
        logger.error(f"MCP server '{self.name}' connection failed after trying all transports: {self.last_error}")
        if last_exception:
            raise last_exception
        raise RuntimeError(f"MCP server '{self.name}' connection failed.")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
        self._tools.clear()
        self.last_error = None
        self.status = MCPServerStatus.DISCONNECTED

    async def call_tools(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._client or self.status != MCPServerStatus.CONNECTED:
            raise RuntimeError(f"MCP client {self.name} is not connected.")

        result = await self._client.call_tool(tool_name, arguments)
        output = []
        for item in result.content:
            if hasattr(item, "text"):
                output.append(item.text)
            else:
                output.append(str(item))

        return {"output": "\n".join(output), "is_error": result.is_error}

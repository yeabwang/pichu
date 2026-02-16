import asyncio
import logging
from dataclasses import dataclass

from config.config import Config
from tools.mcp.client import MCPClient, MCPServerStatus
from tools.mcp.mcp_tool import MCPTool
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPServerSnapshot:
    name: str
    enabled: bool
    transport: str
    status: str
    tool_count: int
    error: str | None = None


class MCPManager:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._clients: dict[str, MCPClient] = {}
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        mcp_config = self._config.mcp_servers
        if not mcp_config:
            logger.info("No MCP servers configured")
            self._initialized = True
            return

        logger.info(f"Initializing {len(mcp_config)} MCP server(s)...")
        self._clients.clear()

        for name, server_config in mcp_config.items():
            if not server_config.enabled:
                logger.info(f"MCP server '{name}' is disabled, skipping")
                continue
            logger.info(f"Creating MCP client for '{name}'")
            self._clients[name] = MCPClient(name=name, config=server_config, cwd=self._config.cwd)

        # Create connection coroutines with timeout
        connection_tasks = [
            asyncio.wait_for(client.connect(), timeout=client.config.startup_timeout_sec)
            for client in self._clients.values()
        ]

        results = await asyncio.gather(*connection_tasks, return_exceptions=True)

        # Log connection results
        for client, result in zip(self._clients.values(), results):
            if isinstance(result, Exception):
                logger.error(f"MCP server '{client.name}' failed to connect: {result}")
            elif client.status == MCPServerStatus.CONNECTED:
                logger.info(f"MCP server '{client.name}' connected with {len(client.tools)} tools")
            else:
                logger.warning(f"MCP server '{client.name}' status: {client.status}")

        self._initialized = True

    def register_tools(self, register: ToolRegistry) -> int:
        # Rebuild MCP tool registrations every time to avoid stale entries after reconnects.
        register.clear_mcp_tools()
        count = 0

        for client in self._clients.values():
            if client.status != MCPServerStatus.CONNECTED:
                logger.warning(f"Skipping tools from '{client.name}' - not connected (status: {client.status})")
                continue

            for tool_info in client.tools:
                tool = MCPTool(
                    config=self._config,
                    tool_info=tool_info,
                    client=client,
                    name=self._tool_name(client.name, tool_info.name),
                )
                register.register_mcp_tool(tool)
                logger.debug(f"Registered MCP tool: {tool.name}")
                count += 1

        logger.info(f"Registered {count} MCP tools total")
        return count

    async def reconnect(self, register: ToolRegistry) -> int:
        await self.shutdown()
        await self.initialize()
        return self.register_tools(register)

    def get_status_snapshot(self) -> list[MCPServerSnapshot]:
        snapshots: list[MCPServerSnapshot] = []
        for name, server_config in self._config.mcp_servers.items():
            client = self._clients.get(name)
            transport = "stdio" if server_config.command else (server_config.transport or "sse")
            if not server_config.enabled:
                status = "disabled"
                error = None
                tool_count = 0
            elif client is None:
                status = "not_initialized"
                error = None
                tool_count = 0
            else:
                status = client.status.value
                error = client.last_error
                tool_count = len(client.tools) if client.status == MCPServerStatus.CONNECTED else 0

            snapshots.append(
                MCPServerSnapshot(
                    name=name,
                    enabled=server_config.enabled,
                    transport=transport,
                    status=status,
                    tool_count=tool_count,
                    error=error,
                )
            )

        return snapshots

    async def shutdown(self) -> None:
        logger.info("Shutting down MCPManager...")
        shutdown_tasks = [client.disconnect() for client in self._clients.values()]
        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        self._clients.clear()
        self._initialized = False
        logger.info("MCPManager shutdown complete.")

    @staticmethod
    def _sanitize_identifier(value: str) -> str:
        sanitized = "".join(char if char.isalnum() or char == "_" else "_" for char in value)
        sanitized = sanitized.strip("_")
        return sanitized or "unnamed"

    @classmethod
    def _tool_name(cls, server_name: str, tool_name: str) -> str:
        # Keep MCP tools namespaced to prevent collisions and simplify hook matching.
        return f"mcp__{cls._sanitize_identifier(server_name)}__{cls._sanitize_identifier(tool_name)}"

# MCP Module

> `tools/mcp/` — MCP server integration for external tools.

## Overview

The MCP module integrates external [Model Context Protocol](https://modelcontextprotocol.io/) servers into Pichu so remote and local tools appear in the same runtime tool registry as built-in tools.

## Public API

```python
from tools.mcp import MCPManager, MCPClient, MCPTool, MCPServerStatus
```

## Architecture

| File | Responsibility |
|------|---------------|
| `client.py` | `MCPClient` — transport-aware client wrapper (stdio, SSE, streamable HTTP) |
| `mcp_manager.py` | `MCPManager` — lifecycle orchestration (init, reconnect, shutdown, registration) |
| `mcp_tool.py` | `MCPTool` — adapts an MCP tool into the internal `Tool` contract |

## Runtime Flow

1. `Session.initialize()` calls `MCPManager.initialize()`.
2. Manager builds enabled clients from `config.mcp_servers` and connects with timeout protection.
3. Connected server tools are wrapped as `MCPTool` instances and registered via `MCPManager.register_tools(...)`.
4. Slash commands (`/mcp`, `/status`, `/doctor`) consume `MCPServerSnapshot` for diagnostics.
5. `Session.__aexit__` calls `MCPManager.shutdown()` to close active MCP clients.

## Configuration

MCP servers are configured in `.pichu/config.toml`:

```toml
[mcp_servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "..." }
```

## Tool Naming

MCP tool names are namespaced as `mcp__<server>__<tool>` to avoid collisions with built-in tools and to improve hook targeting.

## Engineering Guarantees

- MCP registrations are rebuilt on reconnect to prevent stale tools.
- Remote servers support explicit transport selection (`sse`, `http`, `auto`) plus optional headers.
- Status snapshots include disabled/not-initialized/error states for operational diagnostics.

## Extension Points

- Add MCP behavior in manager/client first — commands should consume manager APIs rather than private state.
- Keep transport-level concerns inside `MCPClient`; keep orchestration and registry wiring in `MCPManager`.
- When changing MCP naming or status semantics, update hook/docs expectations and regression tests.

## Related

- [Tool Management](tool-management.md) — tool registry integration
- [Config Module](config-module.md) — `MCPServerConfig` schema
- [Agent Module](agent-module.md) — session initializes MCP manager

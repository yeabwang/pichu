# Tool Management

> `tools/` — tool contracts, registry, execution lifecycle, and selection.

## Overview

The tools subsystem is the execution control plane for pichu. It defines tool contracts, registers built-in and MCP tools, applies guardrails (hooks + approvals), and executes tool calls with consistent result semantics.

## Architecture

| Component             | Responsibility                                                          |
| --------------------- | ----------------------------------------------------------------------- |
| `tools/base.py`     | `Tool` contract, `ToolResult`, `ToolConfirmation`, diff models    |
| `tools/registry.py` | Registration, selection APIs, invocation pipeline with hooks + approval |
| `tools/builtin/`    | Built-in tool implementations and `get_all_builtin_tools()`           |
| `tools/mcp/`        | MCP client, manager, and tool adapters                                  |

## Built-in Tools

| Tool            | Kind    | Description                         |
| --------------- | ------- | ----------------------------------- |
| `read_file`   | Read    | Read file contents                  |
| `write_file`  | Write   | Create or overwrite files           |
| `edit_file`   | Write   | Apply targeted edits                |
| `batch_apply` | Write   | Multiple file operations atomically |
| `shell`       | Execute | Run shell commands                  |
| `list_dir`    | Read    | List directory contents             |
| `grep`        | Read    | Regex search in files               |
| `glob`        | Read    | Find files by pattern               |
| `web_search`  | Read    | Search the web                      |
| `web_fetch`   | Read    | Fetch web page content              |
| `task_create` | Write   | Create a task                       |
| `task_update` | Write   | Update task status                  |
| `task_get`    | Read    | Get task details                    |
| `task_list`   | Read    | List all tasks                      |
| `memory`      | Write   | Manage project memory               |
| `subagent_*`  | Execute | Delegate to sub-agents              |

## Execution Lifecycle

1. **Resolve** — find tool by exact name
2. **Validate** — check parameters against tool schema
3. **PreToolUse hooks** — can modify input or block execution
4. **Approval** — run approval policy for mutating calls (unless hook explicitly allows)
5. **Execute** — run tool logic
6. **PostToolUse hooks** — `PostToolUse` or `PostToolUseFailure`
7. **Return** — normalized `ToolResult` to runtime/UI/model context

## Tool Selection

`ToolRegistry.select_tools(...)` is the canonical way to derive runtime tool sets:

- Supports `allow` and `deny` lists
- Matching is case-insensitive and tolerant to `_`/`-` differences
- Supports glob patterns (`fnmatch`) for ergonomic policies
- Can include/exclude sub-agent and MCP tools explicitly
- Deny rules apply after allow filtering for predictable final sets

## Reliability Guarantees

- Tool names are unique within the built-in namespace.
- MCP tools are separately namespaced (`mcp__<server>__<tool>`).
- Hook and approval decisions are surfaced as structured `ToolResult` errors, not silent drops.

## Extension Points

- Use `select_tools(...)` for scoped tool access (sub-agents, modes, profiles).
- Avoid duplicating tool-name normalization logic outside `ToolRegistry`.
- Keep tool implementations focused on behavior — lifecycle/policy logic belongs in registry + safety layers.

## Related

- [Agent Module](agent-module.md) — drives tool execution in the agentic loop
- [Safety Module](safety-module.md) — approval and sandbox enforcement
- [MCP Module](mcp-module.md) — external tool integration
- [Hooks Module](hooks-module.md) — pre/post tool use hooks

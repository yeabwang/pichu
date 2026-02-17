# Agent Module

> `agent/` — runtime orchestration loop, events, and session lifecycle.

## Overview

The agent module is the runtime core of pichu. It manages the agentic loop (LLM → tools → LLM), emits structured events for UI rendering, and owns session-scoped state.

## Public API

```python
from agent import Agent, Session, AgentEvent, AgentEventType
```

## Architecture

| File | Responsibility |
|------|---------------|
| `agent.py` | Main orchestration loop — hooks → LLM stream → tools → loop detection |
| `session.py` | Session-scoped state and integrations (tools, MCP, memory, tasks, persistence) |
| `events.py` | Typed event contract shared between runtime and UI |

## Design Principles

1. **Event-driven runtime** — the agent emits structured events rather than directly controlling UI rendering.
2. **Session isolation** — mutable state (budgets, context, approvals, hooks, tasks, memory) stays inside `Session`.
3. **Extensible integrations** — MCP tools, sub-agents, hooks, and built-in tools are registered as runtime capabilities.
4. **Lifecycle correctness** — `__aenter__` initializes async resources; `__aexit__` always closes client + MCP manager.
5. **Safety controls first** — tool approval, hook decisions, and loop detection are part of the default execution path.

## Runtime Lifecycle

1. `Agent.__aenter__` initializes session runtime and fires `SessionStart` hooks.
2. `Agent.run(prompt)`:
   - fires `UserPromptSubmit` hook
   - appends user message to context + transcript
   - executes `_agentic_loop`
   - optionally processes `Stop` hook feedback and continues work
   - emits final `AGENT_END`
3. `_agentic_loop` per turn:
   - applies compaction/pruning when needed
   - streams LLM deltas and tool calls
   - invokes tools and records tool results
   - updates token usage statistics
   - runs loop-detection strategy and notifications
4. `Agent.__aexit__` fires `SessionEnd` hook and releases async resources.

## Session Responsibilities

`Session` owns:

- Tool registry (built-in + sub-agent + MCP tools)
- Context manager
- LLM client
- Hook engine
- Approval manager
- Web provenance/budget tracking
- Task manager
- Memory and AGENTS loaders
- Persistence and checkpoint managers

`Session.initialize()` must be called before runtime use so MCP registration and context refresh complete.

## Event Types

| Event | Description |
|-------|-------------|
| `AGENT_START` / `AGENT_END` | Session lifecycle boundaries |
| `TEXT_DELTA` / `TEXT_COMPLETE` | Streaming assistant text |
| `TOOL_CALL_START` / `TOOL_CALL_COMPLETE` | Tool invocation lifecycle |
| `APPROVAL_REQUESTED` / `GRANTED` / `DENIED` | Approval flow |
| `CONTEXT_COMPACTED` / `PRUNED` / `STATS` | Context management |
| `HOOK_FIRED` / `HOOK_BLOCKED` | Hook execution |
| `THINKING_START` / `THINKING_END` | Model reasoning indicators |
| `LLM_RETRY` / `EMPTY_RESPONSE` / `LOOP_DETECTED` | Error and recovery |
| `TASK_*` | Task lifecycle events |

UI code should rely on event payload fields, not internal runtime objects.

## Extension Points

### Add a new runtime event

1. Add enum entry in `AgentEventType`
2. Add builder classmethod in `AgentEvent`
3. Emit event in `agent.py`
4. Handle event in `main.py` / `ui/tui.py`

### Add a new session capability

1. Initialize capability in `Session.__init__` via a dedicated `_init_*` method
2. Inject it into relevant tools/services
3. Keep failures non-fatal for optional features (log and continue)

## Testing

When changing `agent/` behavior, run:

```bash
pytest -q
```

Recommended coverage for runtime changes:

- Event emission order and payload shape
- Hook block/continue behavior
- Loop detection and nudge/stop paths
- Persistence + transcript replay flows

## Related

- [Client Module](client-module.md) — LLM streaming consumed by the agent loop
- [Tool Management](tool-management.md) — tool registry and execution lifecycle
- [Safety Module](safety-module.md) — approval and sandbox enforcement
- [Hooks Module](hooks-module.md) — lifecycle automation

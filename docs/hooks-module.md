# Hooks Module

> `hooks/` — lifecycle automation around agent events.

## Overview

The hooks module runs custom scripts and commands at key points in the agent lifecycle: session start/end, before/after tool use, prompt submission, stop flow, and context compaction.

## Public API

```python
from hooks import HookEngine, HookEvent, HookHandler, HookDecision
```

## Architecture

| File | Responsibility |
|------|---------------|
| `types.py` | Typed contracts — `HookEvent`, `HookMatcher`, `HookHandler`, `HookResult`, `HookFireResult` |
| `engine.py` | Configuration loading, handler resolution, deduplication, sync/async execution |
| `__init__.py` | Stable public exports |

## Hook Events

| Event | Blocking | Description |
|-------|----------|-------------|
| `SessionStart` | No | Session initialized |
| `SessionEnd` | No | Session shutting down |
| `UserPromptSubmit` | Yes | Before processing user input |
| `PreToolUse` | Yes | Before tool execution (can block or modify) |
| `PostToolUse` | No | After successful tool execution |
| `PostToolUseFailure` | No | After failed tool execution |
| `Stop` | Yes | Agent stop signal (loop safety) |
| `SubagentStop` | Yes | Sub-agent stop signal |
| `Notification` | No | General notification |
| `PreCompact` | No | Before context compaction |

## Runtime Flow

1. Runtime calls `HookEngine.fire(event, match_value, extra)`.
2. Engine builds a JSON input payload from `HookInput`.
3. Engine resolves matching handlers and deduplicates equivalent handlers.
4. Sync handlers execute and return `HookResult`; async handlers are scheduled in the background.
5. Caller evaluates `HookFireResult` to determine block/continue behavior.

## Configuration

Hooks are configured in `.PICHU/config.toml`:

```toml
[hooks.pre_tool_use.shell]
command = "echo 'Tool: $TOOL_NAME'"
match = ["shell", "write_file"]
```

## Reliability Contracts

- Matchers are only evaluated for matcher-capable events.
- Blocking semantics are enforced by event type (`HOOK_BLOCKING_EVENTS`).
- Hook output JSON is parsed only on successful exit codes.
- `Stop`/`SubagentStop` includes loop-safety context (`stop_hook_active`) to avoid recursive stop loops.

## Extension Points

- Use `HookEngine.get_registrations()` for UI/debug inspection — avoid private `_hooks` access.
- Keep hook commands deterministic and idempotent where possible.
- Treat hook failures as explicit signal: blocking events can deny actions, non-blocking events should log and continue.

## Related

- [Agent Module](agent-module.md) — fires hooks during runtime lifecycle
- [Config Module](config-module.md) — `HooksConfig` schema
- [Safety Module](safety-module.md) — hooks interact with approval flow

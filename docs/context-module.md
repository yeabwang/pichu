# Context Module

> `context/` — conversation state, token accounting, pruning, and compaction.

## Overview

The context module manages the agent's conversation history, tracks token usage, and handles automatic context window management through pruning and LLM-based compaction.

## Public API

```python
from context import ContextManager, MessageItem, ChatCompactor
```

## Architecture

| File | Responsibility |
|------|---------------|
| `context_manager.py` | In-memory message history, provider-ready payloads, token tracking, pruning |
| `compaction.py` | Formats history for summarization, calls LLM to generate compact summaries |
| `__init__.py` | Stable public exports |

## Runtime Flow

1. Agent appends user/assistant/tool messages through `ContextManager`.
2. On each turn, context checks compression threshold and prune policy.
3. If compression triggers, `ChatCompactor` summarizes history via LLM call.
4. `ContextManager.replace_with_summary(...)` resets history to compact continuation prompts.

## Reliability Contracts

- Hooks and tools rely on deterministic message ordering.
- Pruning only applies after at least two user turns.
- Compaction can return `summary=None` when no useful summary is produced — callers must handle this case.

## Extension Points

- Prefer `ContextManager` public methods (`get_message_token_total`, `get_total_usage`) over private state access.
- Keep context mutations centralized in `ContextManager` to preserve serialization and resume behavior.

## Related

- [Agent Module](agent-module.md) — drives context lifecycle
- [Client Module](client-module.md) — LLM client used for compaction

# Client Module

> `client/` — LLM transport, streaming, and retry logic.

## Overview

The client module is the provider boundary for pichu. It converts raw LLM responses into stable, typed events consumed by the agent runtime, keeping the orchestration loop provider-agnostic.

## Public API

```python
from client import LLMClient, StreamEvent, TokenUsage, ToolCall, ToolResultMessage
```

| Export | Description |
|--------|-------------|
| `LLMClient` | Async completion client (streaming + non-streaming) |
| `StreamEvent` / `StreamEventType` | Normalized event protocol |
| `TokenUsage` | Token accounting payload |
| `ToolCall` / `ToolCallDelta` | Parsed tool invocation data |
| `ToolResultMessage` | Tool result formatted for provider context |

## Stream Event Contract

`LLMClient.chat_completion(...)` yields `StreamEvent` values:

| Event | Description |
|-------|-------------|
| `TEXT_DELTA` | Incremental assistant text chunk |
| `TOOL_CALL_START` | First signal for a tool call (name + ID) |
| `TOOL_CALL_DELTA` | Incremental JSON argument fragment |
| `TOOL_CALL_COMPLETE` | Finalized, parsed tool call payload |
| `MESSAGE_COMPLETE` | Finish reason + token usage |
| `ERROR` | Provider or transport error |

## Usage Example

```python
from client import LLMClient
from config import load_config

config = load_config()
client = LLMClient(config.llm)

messages = [{"role": "user", "content": "Hello"}]
async for event in client.chat_completion(messages, tools=[]):
    if event.type == StreamEventType.TEXT_DELTA:
        print(event.data["content"], end="")
```

## Retry and Reliability

- Retry policy is driven by `config.RetryConfig` (max attempts, backoff).
- Retries apply to transient provider/network failures.
- Register callbacks through `client.on_retry(...)` for UI feedback.
- Error events always carry `event.error` and `event.data["error"]`.

## Extension Points

- Add provider-specific request options in `LLMClient._build_completion_kwargs`.
- Add new stream event types in `client.models.StreamEventType` and emit them in `LLMClient._stream_response`.
- Keep parsing normalization in `client.models` so `agent/` remains protocol-only.

## Related

- [Agent Module](agent-module.md) — consumes client events
- [Config Module](config-module.md) — `LLMConfig` and `RetryConfig`

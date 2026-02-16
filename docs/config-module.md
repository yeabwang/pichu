# Config Module

> `config/` — configuration schema, merge semantics, and singleton access.

## Overview

The config module defines all runtime configuration as typed dataclasses, handles multi-source loading and merge order, and provides process-wide singleton access.

## Public API

```python
from config import Config, load_config, get_config, set_config
```

## Architecture

| File | Responsibility |
|------|---------------|
| `config.py` | Dataclass schema, normalization helpers, singleton access |
| `loader.py` | Source discovery, file parsing, merge orchestration |
| `__init__.py` | Stable public import surface |

## Configuration Sections

| Section | Class | Description |
|---------|-------|-------------|
| `llm` | `LLMConfig` | Model, API key, base URL, temperature, retry |
| `limits` | `LimitsConfig` | Token limits, turn budgets |
| `shell` | `ShellConfig` | Shell execution settings |
| `safety` | `SafetyConfig` | Sandbox, approval policy |
| `logging` | `LoggingConfig` | Console, file, and audit logging |
| `list_dir` | `ListDirConfig` | Directory listing behavior |
| `grep` | `GrepConfig` | Grep tool limits |
| `glob` | `GlobConfig` | Glob tool limits |
| `web_search` | `WebSearchConfig` | Search provider and limits |
| `web_fetch` | `WebFetchConfig` | Fetch timeout and size limits |
| `memory` | `MemoryConfig` | Memory persistence settings |
| `approval` | `ApprovalConfig` | Tool approval policy |
| `hooks` | `HooksConfig` | Hook handler definitions |
| `loop_detection` | `LoopDetectionConfig` | Loop detection thresholds |
| `session` | `SessionPersistenceConfig` | Session storage settings |
| `mcp_servers` | `dict[str, MCPServerConfig]` | MCP server connection configs |

## Merge Order

`load_config(...)` applies configuration in this order (later overrides earlier):

1. Dataclass defaults
2. System config — `~/.pichu/config.toml`
3. Project config — `.pichu/config.toml` (searched upward from cwd)
4. System `.env` — `~/.pichu/.env` (keys saved by `/login`)
5. Project `.env` — `.env` from the current working directory
6. Environment overrides — `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `PICHU_DEBUG`
7. Developer instructions — `AGENT.md` in the nearest discovered `.pichu` directory

## Typed Section Normalization

- **Hooks** — normalized into `HooksConfig` → `HookMatcherConfig` → `HookHandlerConfig`
- **MCP servers** — normalized into `MCPServerConfig` objects (stdio + remote transport, headers)
- **Logging** — `LoggingConfig` with console/file/audit sinks and redaction policy
- **Enum fields** (e.g., approval policy) — coerced from string values

## Extension Points

- Add new config sections as dataclasses on `Config`.
- If a section needs custom parsing from TOML, extend the normalization helpers in `config.config`.
- Keep loader behavior deterministic: source discovery and merge order should remain explicit and test-backed.

## Related

- [Deployment](deployment.md) — environment variable reference
- [Safety Module](safety-module.md) — `SafetyConfig` usage
- [Hooks Module](hooks-module.md) — `HooksConfig` usage
- [MCP Module](mcp-module.md) — `MCPServerConfig` usage

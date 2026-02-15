# Utils Module

> `utils/` — shared runtime utilities for sessions, memory, tasks, web tools, and more.

## Overview

The utils module provides infrastructure used across the agent: session/checkpoint persistence, memory management, task helpers, sub-agent loading, web-tool safety, and common support utilities.

## Subsystems

### Memory

| File | Responsibility |
|------|---------------|
| `memory_types.py` | `Memory`, `MemoryCategory`, `MemoryStore` — typed models and in-memory operations |
| `memory_storage.py` | `MemoryStorage` — file-backed persistence with backup recovery |
| `memory_manager.py` | `MemoryManager` — high-level API for save, search, view, and context generation |

### Tasks

| File | Responsibility |
|------|---------------|
| `task_types.py` | `Task`, `TaskList` — state, dependencies, metadata |
| `task_storage.py` | File-backed persistence with atomic writes and lock files |
| `task_manager.py` | Business rules, state transitions, lock-aware mutations |

See [Task Management](task-management.md) for state machine and coordination details.

### Sessions and Rewind

| File | Responsibility |
|------|---------------|
| `session_storage.py` | Session metadata/transcript storage, `resolve_session_reference(...)` |
| `checkpoint_manager.py` | Turn-based file snapshot and restore logic |

`resolve_session_reference(...)` is the canonical resolver for CLI and `/sessions` — it handles exact ID → prefix → exact title → partial title with explicit ambiguity errors.

### Sub-agents

| File | Responsibility |
|------|---------------|
| `subagent_types.py` | Sub-agent config and result contracts |
| `subagent_loader.py` | Markdown loading from user and project directories |
| `subagent_transcript.py` | Per-sub-agent transcript persistence |

See [Sub-agents Module](subagents-module.md) for orchestration details.

### Web Tool Support

| File | Responsibility |
|------|---------------|
| `web_types.py` | Structured web/search payload types |
| `web_security.py` | URL safety and provenance checks |
| `cache.py` | Caching and rate-limit helpers |

### Runtime Helpers

| File | Responsibility |
|------|---------------|
| `runtime_logging.py` | Logging bootstrap, redaction, audit events |
| `paths.py` | Path resolution utilities |
| `text.py` | Text processing helpers |
| `loop_detector.py` | Loop detection strategies |
| `exceptions.py` | Shared exception types |
| `agents_loader.py` | `AGENTS.md` file parsing and context extraction |
| `sandbox.py` | Compatibility shim (moved to `safety.sandbox`) |

## Reliability Contracts

- Utility dataclasses define stable serialization boundaries used by storage and runtime.
- Storage helpers are filesystem-oriented and designed for deterministic read/write flows.
- Web security utilities centralize URL validation/provenance checks (callers do not duplicate policy logic).

## Extension Points

- Prefer importing existing utility types/managers instead of re-implementing persistence or matching logic.
- Keep utility APIs behavior-focused and provider-agnostic for reusability across `agent/`, `commands/`, and `tools/`.
- For user-provided session identifiers, always use `resolve_session_reference(...)` to keep matching and ambiguity handling consistent.

## Related

- [Agent Module](agent-module.md) — session uses most utils subsystems
- [Task Management](task-management.md) — detailed task system docs
- [Sub-agents Module](subagents-module.md) — sub-agent orchestration
- [Logging Module](logging-module.md) — runtime logging details
- [Safety Module](safety-module.md) — sandbox (moved from utils)

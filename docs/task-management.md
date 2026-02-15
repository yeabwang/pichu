# Task Management

> Task orchestration — planning, dependencies, persistence, and multi-session coordination.

## Overview

The task system supports complex, multi-step work where the agent needs explicit planning, durable progress across sessions, dependency-aware execution, and safe coordination in shared task lists.

## Architecture

| Component | Responsibility |
|-----------|---------------|
| `utils/task_types.py` | `Task` and `TaskList` — state, dependencies, metadata, in-memory graph operations |
| `utils/task_storage.py` | File-backed persistence under `~/.pichu/tasks/`, atomic writes, lock file with stale recovery |
| `utils/task_manager.py` | Business rules, state transitions, lock-aware read/modify/write mutations |
| `tools/builtin/task_*.py` | Tool contracts for the model (`task_create`, `task_update`, `task_get`, `task_list`) |
| `commands/builtin/tasks.py` | Human-facing `/tasks` command for CLI inspection |

## State Machine

Task status transitions:

```
pending ──→ in_progress ──→ completed
   ↑              │
   └──────────────┘
      (requeue)
```

| Transition | Method |
|-----------|--------|
| `pending → in_progress` | `TaskManager.start_task` |
| `in_progress → completed` | `TaskManager.complete_task` |
| `in_progress → pending` | `TaskManager.requeue_task` |

Invalid transitions (e.g., `pending → completed`) fail fast with `TaskManagerError`.

## Dependency Model

- `blocked_by` — tasks that must finish first
- `blocks` — inverse relationship (tasks waiting on this task)

`TaskManager.add_dependency(task_id, blocker_id)` enforces:

- Both tasks must exist
- No self-dependency
- No transitive cycles (graph path check before linking)

When a task is completed, dependent tasks are automatically unblocked.

## Persistence and Coordination

Every mutating manager operation follows this sequence:

1. Acquire list lock
2. Reload latest task list snapshot
3. Apply mutation
4. Persist (when `auto_save=True`)
5. Release lock

This prevents lost updates when multiple sessions share one `PICHU_TASK_LIST_ID`.

## Tool Contracts

| Tool | Behavior |
|------|----------|
| `task_create` | Requires initialized task manager; errors if persistence unavailable |
| `task_update` | Routes lifecycle updates through `TaskManager` with strict transition rules |
| `task_get` | Reads current manager snapshot |
| `task_list` | Reports all tasks with status |

## Extension Points

1. Put lifecycle/dependency rules in `TaskManager`, not tool handlers.
2. Keep state transitions explicit and testable.
3. Preserve lock-aware mutation flow for any new write path.
4. Add regression tests for transition rules, cycle prevention, and cross-session behavior.

## Related

- [Agent Module](agent-module.md) — session initializes task manager
- [Utils Module](utils-module.md) — task storage utilities
- [Tool Management](tool-management.md) — task tools in the registry

# Safety Module

> `safety/` — approval policies, sandbox enforcement, and command risk classification.

## Overview

The safety module is the central guardrail layer for mutating tool execution and filesystem access. It combines policy decisions, command risk checks, and sandbox enforcement so tool code stays focused on behavior.

## Architecture

| File                  | Responsibility                                                                                   |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `approval_types.py` | Shared `ApprovalDecision` and `ApprovalResponse` enums                                       |
| `command_safety.py` | Command normalization, dangerous-command detection, safe-command classification                  |
| `rule_engine.py`    | Permission rule parsing and evaluation (`deny → ask → allow`)                                |
| `approval.py`       | Approval manager orchestration (policy, rules, memory, callback prompt flow)                     |
| `sandbox.py`        | Filesystem sandbox — read/write checks, allowed-directory boundaries, sensitive path protection |

## Runtime Integration

- `Session` initializes the filesystem sandbox from config at session startup.
- Tool invocation approval is enforced by `tools.registry` via `ApprovalManager`.
- File and search tools call sandbox checks before reading or writing.

## Configuration

Safety settings live in `.PICHU/config.toml`:

```toml
[safety.sandbox]
enabled = true
restrict_to_cwd = true
allowed_directories = ["/tmp"]
block_executable_writes = true

[approval]
default_policy = "auto-edit"  # allow | auto-edit | ask | deny
```

### Sandbox Options

| Option                        | Default   | Description                                 |
| ----------------------------- | --------- | ------------------------------------------- |
| `enabled`                   | `true`  | Enable filesystem sandboxing                |
| `restrict_to_cwd`           | `true`  | Limit writes to the working directory       |
| `allowed_directories`       | `[]`    | Additional directories permitted for writes |
| `block_executable_writes`   | `true`  | Block creation of executable files          |
| `restrict_reads_to_allowed` | `false` | Also restrict reads to allowed directories  |

## Extension Points

- Add command risk patterns in `safety.command_safety` (single source of truth).
- Add new permission matching behavior in `safety.rule_engine` (keep deterministic order).
- Keep tool-specific logic out of approval internals — use `ToolConfirmation` metadata to surface intent/diff/path details.

## Related

- [Tool Management](tool-management.md) — approval checks during tool execution
- [Agent Module](agent-module.md) — session initializes sandbox and approval manager
- [Config Module](config-module.md) — `SafetyConfig` and `ApprovalConfig` schemas
- [Logging Module](logging-module.md) — audit events for denied approvals

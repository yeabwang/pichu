# Sub-agents Module

> Sub-agent orchestration — delegation, isolation, and scoped tool access.

## Overview

The sub-agent system lets the primary agent delegate scoped work to specialized agents with isolated context, model overrides, and tool-level constraints. Sub-agents are defined as markdown files with YAML frontmatter.

## Architecture

| Component | Responsibility |
|-----------|---------------|
| `subagents/loader.py` | Loads markdown definitions from bundled specs, user (`~/.pichu/sub_agents/`), and project (`sub_agents/`) directories |
| `subagents/types.py` | Typed contracts for sub-agent config and execution results |
| `subagents/transcript.py` | Per-sub-agent transcript persistence for resume/debug |
| `subagents/specs/*.md` | Bundled default sub-agent definitions shipped with the package |
| `tools/builtin/subagent.py` | Runtime orchestration — `SubAgentTool` execution, hooks, and background tasks |

## Defining a Sub-agent

Create a markdown file in `sub_agents/` with YAML frontmatter:

```markdown
---
name: security-reviewer
description: Reviews code for security vulnerabilities
tools:
  - read_file
  - grep
  - glob
disallowedTools:
  - shell
  - write_file
maxTurns: 10
---

You are a security reviewer. Analyze the provided code for...
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique sub-agent name (lowercase letters, numbers, hyphens) |
| `description` | Yes | What this sub-agent does (used for delegation) |
| `tools` | No | Allowlist of tools this agent can use |
| `disallowedTools` | No | Denylist of tools to exclude |
| `maxTurns` | No | Maximum turns before forced stop |
| `model` | No | Model override for this agent |
| `skills` | No | Skills available to this sub-agent |
| `permissionMode` | No | Permission mode override (e.g., `on_request`, `auto`) |
| `hooks` | No | Hook configuration specific to this sub-agent |
| `color` | No | Display color for UI differentiation |

## Runtime Flow

1. Session loads sub-agent definitions and registers one dynamic tool per agent (`subagent_<name>`).
2. Main agent invokes `subagent_<name>` with prompt, context, and resume options.
3. Sub-agent tool creates isolated message history and transcript state.
4. Tool loop runs with scoped tools and permission/hook checks.
5. Output and metadata return to the parent agent (or background task status API).

## Reliability Contracts

- Sub-agents cannot invoke other sub-agents (nesting is blocked by tool filtering).
- Tool access is deterministic: allowlist → denylist → permission mode.
- Hook commands run asynchronously and can block operations via exit code `2`.
- Completed background tasks are cleaned from in-memory tracking after status retrieval.
- `maxTurns` in frontmatter is honored per sub-agent with safe fallback.

## Built-in Sub-agents

The project includes 9 pre-built sub-agent definitions:

| Agent | Purpose |
|-------|---------|
| `architect` | System architecture analysis |
| `build-error-resolver` | Build error diagnosis and fixes |
| `code-reviewer` | Code review and quality checks |
| `database-reviewer` | Database schema and query review |
| `doc-updater` | Documentation maintenance |
| `planner` | Task planning and breakdown |
| `refactor-cleaner` | Code refactoring guidance |
| `security-reviewer` | Security vulnerability analysis |
| `tdd-guide` | Test-driven development guidance |

## Extension Points

- Keep sub-agent descriptions specific — delegation quality depends on description clarity.
- Prefer explicit `tools` + `disallowedTools` for safety-critical agents.
- Use `maxTurns` for bounded-cost specialist agents.
- Use `SubAgentLoader.parse_content(...)` in tests to validate frontmatter behavior.

## Related

- [Agent Module](agent-module.md) — session registers sub-agent tools
- [Tool Management](tool-management.md) — tool selection and scoping
- [Safety Module](safety-module.md) — permission modes for sub-agent tools

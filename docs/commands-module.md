# Commands Module

> `commands/` — slash command parsing, registration, dispatch, and custom command loading.

## Overview

The commands module owns the `/command` system used in interactive sessions. It supports 29 built-in commands, alias resolution, fuzzy suggestions, and markdown-backed custom commands.
Command handlers return `CommandResult` and can provide `CommandDisplayPayload` for richer UI rendering.

## Architecture

| Component | Responsibility |
|-----------|---------------|
| `commands.base` | `SlashCommand` interface, `CommandSpec` metadata, `CommandRegistry`, `CommandDisplayPayload` |
| `commands.router` | Parses `/command args`, dispatches, returns `CommandResult` |
| `commands.builtin` | Built-in command catalog and registration |
| `commands.custom_loader` | Loads markdown-backed custom commands from global and project scopes |

## Runtime Flow

1. `create_command_router(project_root=...)` builds registry + router.
2. Built-in commands are registered first.
3. Custom commands are loaded (global `~/.pichu/commands/`, then project `.pichu/commands/` overrides).
4. `CommandRouter.dispatch(...)` resolves command name or alias and executes the handler.

## Modular Scaffolding Commands

Project scaffolding follows a modular ownership model:

- `/init` is a lightweight orchestrator.
- `/agent init` owns AGENTS file scaffolding (`AGENTS.md`, `AGENTS.local.md`, gitignore entry).
- `/memory init` owns memory storage scaffolding (`.pichu/memory/` state/cursor).
- `/cache init` owns cache scaffolding (`.pichu/cache/web_cache.db`).
- `/hooks init` owns hook script/config scaffolding (`.pichu/hooks/*` + config hook handlers).

Each init command is idempotent and can be run independently.

## Creating a New Built-in Command

```python
from commands.base import CommandDisplayPayload, CommandResult, SlashCommand

class MyCommand(SlashCommand):
    name = "mycommand"
    description = "Does something useful"
    usage = "/mycommand [args]"
    aliases = ["mc"]

    async def execute(self, args: str, session, tui, config) -> CommandResult:
        # Implement your command logic
        return CommandResult(
            output="Done!",
            display=CommandDisplayPayload(renderables=["Done!"]),
        )
```

Built-in commands are discovered automatically from `commands/builtin/*.py` when they subclass `SlashCommand`.

## Engineering Guarantees

- Duplicate command names are rejected unless explicit replacement is requested.
- Alias collisions are validated at registration time.
- Unknown commands trigger fuzzy suggestions from registry metadata.
- Help output is generated from registered `CommandSpec` objects (never hardcoded).

## Related

- [Agent Module](agent-module.md) — session context passed to command handlers
- [Config Module](config-module.md) — config object passed to command handlers

# Commands Module

> `commands/` — slash command parsing, registration, dispatch, and custom command loading.

## Overview

The commands module owns the `/command` system used in interactive sessions. It supports 28 built-in commands, alias resolution, fuzzy suggestions, and markdown-backed custom commands.

## Architecture

| Component | Responsibility |
|-----------|---------------|
| `commands.base` | `SlashCommand` interface, `CommandSpec` metadata, `CommandRegistry` |
| `commands.router` | Parses `/command args`, dispatches, returns `CommandResult` |
| `commands.builtin` | Built-in command catalog and registration |
| `commands.custom_loader` | Loads markdown-backed custom commands from global and project scopes |

## Runtime Flow

1. `create_command_router(project_root=...)` builds registry + router.
2. Built-in commands are registered first.
3. Custom commands are loaded (global `~/.pichu/commands/`, then project `.pichu/commands/` overrides).
4. `CommandRouter.dispatch(...)` resolves command name or alias and executes the handler.

## Creating a New Built-in Command

```python
from commands.base import SlashCommand, CommandResult

class MyCommand(SlashCommand):
    name = "mycommand"
    description = "Does something useful"
    usage = "/mycommand [args]"
    aliases = ["mc"]

    async def execute(self, args: str, *, session, tui, config) -> CommandResult:
        # Implement your command logic
        return CommandResult(output="Done!")
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

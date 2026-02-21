# UI Module

> `ui/` — terminal UI composition, event routing, prompt input, rendering, and runtime task status.

## Overview

The UI module separates terminal concerns into focused layers so runtime events, command output rendering, and prompt input handling evolve independently.

## Architecture

| Component | Responsibility |
|-----------|----------------|
| `ui/tui.py` | UI composition root; wires console, input manager, renderer, and event router mixin |
| `ui/controller/event_router.py` | Maps agent/tool/runtime events into rich renderables and interactive prompts |
| `ui/output/renderer.py` | Differential renderer for streamed output, output block spacing, and debounced footer refresh |
| `ui/input/manager.py` | Prompt toolkit integration, slash fuzzy completion, `@path` completion, and key bindings |
| `ui/runtime/task_runner.py` | Background runtime task runner with queue/progress/completion/cancellation events |
| `ui/capabilities/detection.py` | Detects TTY/color capabilities and terminal constraints (`NO_COLOR`, `TERM=dumb`) |
| `ui/contracts/models.py` | Shared UI contracts (`UIEvent`, `CommandChoice`, `CommandDisplayPayload`) |
| `ui/theme.py` | One Dark theme palette, prompt style rules, and theme presets |

## Runtime Flow

1. `TUI` initializes console behavior from capability detection and sets up the renderer/input manager.
2. Agent and tool lifecycle events are routed through `TUIEventRouterMixin`.
3. The renderer streams assistant deltas, renders command payloads, and updates deferred footer state.
4. Runtime background tasks emit `QUEUED`, `STARTED`, `PROGRESS`, `COMPLETED`, `FAILED`, and `CANCELLED` status events.
5. CLI idle loops drain runtime events so progress remains visible while waiting for user input.

## Prompt UX Behavior

- Slash completion is fuzzy and alias-aware.
- `@` path completion works at prompt start and in mid-sentence text.
- Key bindings:
  - `Enter` submit
  - `Esc` + `Enter` newline
  - `Ctrl+S` submit
  - `Esc` interrupt active agent run (interactive TTY only)
- `AGENT_UI_KEYMAP=vi` enables vi editing mode; emacs is the default.

## Non-TTY and Fallback Behavior

- Non-interactive environments skip prompt-toolkit input and live Rich render loops.
- Dumb terminals and `NO_COLOR` disable colorized rendering.
- Output still renders deterministically via plain console surfaces.

## Related

- [Agent Module](agent-module.md) — event producers consumed by the UI
- [Commands Module](commands-module.md) — command output payload contracts
- [Usage Guide](usage.md) — end-user interaction overview

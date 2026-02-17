# Usage Guide

## Run Modes

```bash
# Interactive mode
pichu

# Single prompt mode
pichu "summarize this codebase"
```

On first interactive launch in a new directory, pichu asks you to confirm the workspace is trusted before granting file edit and shell execution capabilities.
The trust decision is persisted per workspace in `~/.pichu/trusted_workspaces.json`.
If a workspace is not trusted yet, non-interactive mode exits safely and asks you to run interactive mode once to approve it.

## CLI Options

```text
pichu [PROMPT]
  -c, --cwd PATH            Set working directory
  -C, --continue            Continue latest session in this directory
  -r, --resume ID_OR_TITLE  Resume a specific session
```

## Core Slash Commands

- `/help` — list all commands
- `/login` — configure model/provider credentials
- `/init` — bootstrap project config and guidance files
- `/doctor` — installation/config health check
- `/status` — current model/provider/session state
- `/mcp` — inspect/manage MCP server status
- `/sessions` — list/manage saved sessions
- `/tasks` — inspect/manage task list

Use `/help` in-session for the full command list.

## Configuration Loading Order

Later sources override earlier ones:

1. Code defaults
2. `~/.pichu/config.toml`
3. `.pichu/config.toml` (search upward from cwd)
4. `~/.pichu/.env`
5. `.env` in current working directory
6. Environment variables (for example `LLM_API_KEY`)

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `LLM_API_KEY` | Yes | API key for your LLM provider |
| `SERPER_API_KEY` | No | API key for web search (Serper) |
| `PICHU_DEBUG` | No | Enable debug logging (`true`/`false`) |
| `PICHU_PROJECT_DIR` | No | Project config directory name (default: `.pichu`) |
| `PICHU_CONFIG_FILE` | No | Config file name (default: `config.toml`) |

## Troubleshooting Flow

1. Run `/doctor`
2. Run `/status`
3. Run `/debug`
4. Check logs in `logs/app/` and `logs/security/audit.log`

Common issues:

- API auth/provider issues: rerun `/login`
- MCP connection issues: inspect `/mcp`
- Sandbox denials: review `[safety.sandbox]` in `.pichu/config.toml`

<p align="center">
  <img src="assets/logo.png" alt="pichu Logo" width="200"><br>

  <h1 align="center">⚡ pichu</h1>

  <p align="center">
    <strong>Code, compile, conquer.</strong><br>
    Open-source coding agent that lives in your terminal.
  </p>

  <!-- Badges -->
  <p align="center">
    <img src="https://static.pepy.tech/personalized-badge/pichu?period=total&units=INTERNATIONAL_SYSTEM&left_color=black&right_color=green&left_text=downloads">
    <img src="https://img.shields.io/pypi/v/pichu">
    <img src="https://img.shields.io/github/license/yeabwang/pichu">
    <img src="https://img.shields.io/github/stars/yeabwang/pichu?style=social">
  </p>

  <!-- Links -->
  <p align="center">
    <a href="https://github.com/yeabwang/pichu">Home</a> •
    <a href="https://github.com/yeabwang/pichu/stargazers">Star</a> •
    <a href="https://github.com/yeabwang/pichu/issues">Report Bug</a> •
    <a href="https://github.com/yeabwang/pichu/pulls">Submit PR</a> •
    <a href="https://pypi.org/project/pichu/">PyPI</a>
  </p>
</p>

---

<p align="center">
  <em>Lightweight. Deterministic. Hackable.</em>
</p>


<p align="center">
  <img src="assets/demo.gif" alt="pichu Demo" />
</p>

## Features

* **Composable tool stack** — files, shell, web, tasks, and memory in one agent
* **Sub-agents & task orchestration** — delegate, isolate, and coordinate complex workflows
* **MCP ecosystem integration** — connect external MCP servers as native tools
* **Context management** — token-aware compaction, pruning, and usage tracking
* **Session management** — persistent transcripts, resume, rewind, and fork sessions
* **Memory system** — global and project memory with structured retrieval
* **Hooks & automation** — lifecycle hooks for tool use, compaction, and agent control
* **Interactive terminal UX** — 29 slash commands for runtime control and diagnostics
* **Safety & reliability** — workspace trust prompt, sandboxing, approvals, retries, and audit logging

## Quick Start

1. Install pichu (recommended: one-line installer):
   - See [docs/install.md](docs/install.md)
2. Launch:
   - `pichu`
3. Configure model/provider:
   - `/login`
4. Initialize project:
   - `/init`

## Quick Demo

```bash
# Start interactive mode
pichu

# Configure model/provider inside the session
/login

# Initialize project
/init

# Ask for a one-off task
pichu "explain this repo"
```

## Documentation

### Getting Started

- [Quick Install](docs/install.md)
- [Usage Guide](docs/usage.md)

### Development and Operations

- [Development Guide](docs/development.md)
- [Deployment Guide](docs/deployment.md)

### Module and Architecture References

- [Agent Module](docs/agent-module.md) — runtime loop, events, session lifecycle
- [Client Module](docs/client-module.md) — LLM client, streaming, retry
- [Commands Module](docs/commands-module.md) — slash command system
- [Config Module](docs/config-module.md) — configuration schema and loading
- [Context Module](docs/context-module.md) — context management and compaction
- [Hooks Module](docs/hooks-module.md) — lifecycle hook engine
- [Logging Module](docs/logging-module.md) — runtime and audit logging
- [MCP Module](docs/mcp-module.md) — MCP server integration
- [Safety Module](docs/safety-module.md) — approval and sandbox policies
- [Sub-agents Module](docs/subagents-module.md) — sub-agent orchestration
- [Task Management](docs/task-management.md) — task system architecture
- [Tool Management](docs/tool-management.md) — tool registry and execution
- [Utils Module](docs/utils-module.md) — shared runtime utilities

## Support the Project

If you find this project useful:

- ⭐ Star it on GitHub to show support
- 🐛 Open issues to report bugs or suggest features
- 🔧 Submit a PR to improve the project
- 💡 Share it with others who might benefit

Contributions of any size are welcome.

## License

Apache 2.0 — see [LICENSE](LICENSE).

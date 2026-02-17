# Quick Install

## Prerequisites

- Python 3.11+
- LLM provider API key

## Install

### Recommended: one-line installer (auto setup)

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/yeabwang/pichu/main/install.ps1 | iex
```

Windows CMD:

```cmd
curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.cmd -o install.cmd && install.cmd
```

The installer automatically:

- installs with `uv`, `pipx`, or `pip` (in that order)
- saves the install directory to your user PATH
- saves a `pichu` command alias in your shell/profile
- prints a clear “restart your shell” message when needed

### Package manager alternatives

```bash
# uv (recommended)
uv tool install pichu

# pipx
pipx install pichu

# pip
pip install pichu
```

If `pichu` is not found after install, restart your shell once.

## First Run

```bash
pichu
/login
/init
```

- On first interactive launch in a new folder, pichu shows a workspace trust confirmation before allowing file edits and shell execution.
- Trust is saved per workspace in `~/.pichu/trusted_workspaces.json`.
- If you run non-interactive mode in an untrusted workspace (for example `pichu "..."`), pichu exits safely and asks you to approve once in interactive mode first.

- `/login` configures provider, model, and API key.
- `/init` bootstraps the core project scaffold and runs subsystem initializers:
  `/agent init`, `/memory init`, `/cache init`, and `/hooks init`.

## Verify

```bash
pichu --help
pichu "explain this repository"
```

## Next

- [Usage Guide](usage.md)
- [Development Guide](development.md)
- [Deployment Guide](deployment.md)

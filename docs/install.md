# Quick Install

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended)
- LLM provider API key

## Install

### Option A (Recommended): uv tool install

```bash
uv tool install pichu
```

### Option B: pip

```bash
pip install pichu
```

### Option C: Linux/macOS installer script

```bash
curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.sh | bash
```

## First Run

```bash
pichu
/login
/init
```

- `/login` configures provider, model, and API key.
- `/init` creates `AGENTS.md`, `AGENTS.local.md`, and `.pichu/config.toml` in your project.

## Verify

```bash
pichu --help
pichu "explain this repository"
```

## Next

- [Usage Guide](usage.md)
- [Development Guide](development.md)
- [Deployment Guide](deployment.md)

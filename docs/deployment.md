# Deployment Guide

> Docker, CI, PyPI publishing, and runtime configuration.

## Install

### One-line install (Linux / macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/yeabwang/pichu/main/install.sh | bash
```

### Package managers

```bash
# uv (recommended)
uv tool install pichu

# pip
pip install pichu

# pipx
pipx install pichu
```

### From source

```bash
git clone https://github.com/yeabwang/pichu.git && cd pichu
uv venv && uv pip install -e .
/init          # generate AGENTS.md and .pichu/config.toml in the current project

# Or use requirements files
git clone https://github.com/yeabwang/pichu.git && cd pichu
uv venv
uv pip install -r requirements.txt && uv pip install -e .
/init          # generate AGENTS.md and .pichu/config.toml in the current project
```

### Verify

```bash
pichu --help
```

Web tooling (search, fetch, PDF) is included in the main dependencies:

```bash
uv pip install -r requirements.txt
```

### Requirements Files

| File | Contents |
|------|----------|
| `requirements.txt` | Runtime dependencies (core + web tooling) |
| `requirements-dev.txt` | Core + testing, linting, and build tools |

## Docker

### Build

```bash
docker build -t pichu:latest .
```

### Run

```bash
# Linux / macOS
docker run --rm -it \
  -e LLM_API_KEY=your-key \
  -v "$(pwd)":/workspace \
  pichu:latest

# Windows PowerShell
docker run --rm -it `
  -e LLM_API_KEY=your-key `
  -v "${PWD}:/workspace" `
  pichu:latest
```

### Notes

- The workspace is mounted at `/workspace` inside the container.
- `.env` files are excluded from the image via `.dockerignore` — pass secrets as environment variables.
- The image uses `python:3.13-slim` as its base.

## PyPI Publishing

Build and upload to PyPI:

```bash
uv pip install build twine
python -m build
twine upload dist/*
```

For TestPyPI first:

```bash
twine upload --repository testpypi dist/*
uv pip install --index-url https://test.pypi.org/simple/ pichu
```

## CI

GitHub Actions workflow: `.github/workflows/ci.yml`

The pipeline runs on every push and pull request:

1. **Checkout** — `actions/checkout@v4`
2. **Install uv** — `astral-sh/setup-uv@v5`
3. **Setup Python** — 3.11+ on `ubuntu-latest`
4. **Install** — `uv pip install --system -e .[dev]`
5. **Test** — `pytest -q`
6. **Build** — `python -m build`

To avoid cross-filesystem hardlink warnings in CI, set:

```bash
export UV_LINK_MODE=copy
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | API key for your LLM provider |
| `LLM_BASE_URL` | No | OpenRouter URL | Provider API endpoint |
| `LLM_MODEL` | No | Not set (run `/login`) | Model identifier |
| `SERPER_API_KEY` | No | — | Web search API key |
| `PICHU_DEBUG` | No | `false` | Enable debug logging |
| `PICHU_PROJECT_DIR` | No | `.pichu` | Project config directory name |
| `PICHU_CONFIG_FILE` | No | `config.toml` | Config file name |

Project-level config should live in `.pichu/config.toml`. See [Config Module](config-module.md) for full schema.

## Related

- [Config Module](config-module.md) — configuration schema and merge logic
- [README](../README.md) — quick start guide

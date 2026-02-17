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
pichu          # then run /init inside the interactive session

# Or use requirements files
git clone https://github.com/yeabwang/pichu.git && cd pichu
uv venv
uv pip install -r requirements.txt && uv pip install -e .
pichu          # then run /init inside the interactive session
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

### Automatic stable releases from GitHub Releases

The repository is configured to publish to PyPI when a **non-draft, non-prerelease** GitHub Release is published.

One-time setup:

1. Create a `release` environment in your GitHub repository settings (if it does not already exist).
2. In PyPI, add this repository as a Trusted Publisher for the project:
   - **Owner**: your GitHub org/user
   - **Repository**: `pichu`
   - **Workflow file**: `.github/workflows/release.yml`
   - **Environment name**: `release`
3. Ensure `pyproject.toml` `project.version` matches the release tag (for example, tag `v0.1.1` for version `0.1.1`).
4. Publish a GitHub Release with a `v*` tag to trigger the workflow.

### Manual upload (fallback)

Build and upload to PyPI manually:

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

The pipeline runs on every push to `main`/`develop` and on pull requests:

| Job | Description |
|-----|-------------|
| **Pre-commit** | Runs all pre-commit hooks (`uv run pre-commit run --all-files`) |
| **Lint & Format** | Ruff lint + format check |
| **Type Check** | Mypy across all `src/` modules and `main.py` |
| **Tests** | `pytest` with coverage on Python 3.13. Coverage uploaded to Codecov. |
| **Security Audit** | Bandit security rules via Ruff (`ruff check --select S`) |
| **Build Package** | `uv build` — produces sdist + wheel (requires all prior jobs to pass) |

Environment setup uses a shared composite action (`.github/actions/setup-env/`) that installs uv, Python, system build deps (libxml2, libxslt1, zlib1g), and syncs project dependencies.

Additional workflows:
- **Dependency Review** (`.github/workflows/dependency-review.yml`) — runs on PRs to main, fails on high-severity dependency issues.
- **Release** (`.github/workflows/release.yml`) — triggered when a GitHub Release is published, publishes to PyPI via OIDC for stable releases.

To avoid cross-filesystem hardlink warnings in CI, the pipeline sets:

```bash
export UV_LINK_MODE=copy
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | API key for your LLM provider |
| `SERPER_API_KEY` | No | — | Web search API key |
| `PICHU_DEBUG` | No | `false` | Enable debug logging |
| `PICHU_PROJECT_DIR` | No | `.pichu` | Project config directory name |
| `PICHU_CONFIG_FILE` | No | `config.toml` | Config file name |

Project-level config should live in `.pichu/config.toml`. See [Config Module](config-module.md) for full schema.

## Related

- [Config Module](config-module.md) — configuration schema and merge logic
- [README](../README.md) — quick start guide

# Development Guide

## Local Setup

```bash
git clone https://github.com/yeabwang/pichu.git
cd pichu
uv venv
uv sync --extra dev
uv run pre-commit install
```

Alternative setup:

```bash
uv venv
uv pip install -r requirements-dev.txt
uv pip install -e .
```

## Common Commands

```bash
make dev
make format
make lint
make typecheck
make test
make test-cov
make build
make clean
```

## Test and Build

```bash
uv run pytest -q
uv run pytest -q tests/test_ui_capabilities.py tests/test_ui_input.py tests/test_ui_render.py tests/test_ui_runtime_phase4.py
uv build
```

## Project Bootstrap

Inside a pichu session, run:

```bash
/init
```

This generates baseline project guidance and config files.

## Related Docs

- [Deployment Guide](deployment.md)
- [Config Module](config-module.md)
- [UI Module](ui-module.md)
- [Tool Management](tool-management.md)
- [Task Management](task-management.md)

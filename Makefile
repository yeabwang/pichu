.PHONY: dev format lint typecheck test test-cov build clean

MYPY_TARGETS = src/agent/ src/client/ src/commands/ src/config/ src/context/ src/hooks/ src/safety/ src/subagents/ src/tools/ src/ui/ src/utils/ main.py

dev:
	uv sync --extra dev
	uv run pre-commit install

format:
	uv run ruff check . --fix
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy $(MYPY_TARGETS)

test:
	uv run pytest -q

test-cov:
	uv run pytest --cov --cov-report=xml --cov-report=term-missing

build:
	uv build

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['build', 'dist', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov']]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').glob('*.egg-info')]; [p.unlink() for p in [pathlib.Path('.coverage'), pathlib.Path('coverage.xml')] if p.exists()]"

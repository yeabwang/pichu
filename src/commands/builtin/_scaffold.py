"""Shared helpers for modular project scaffolding commands."""

from __future__ import annotations

import os
from pathlib import Path

_CORE_PROJECT_CONFIG_TEMPLATE = """# pichu project configuration
# Keep this file lightweight; initialize subsystems via:
#   /agent init
#   /memory init
#   /cache init
#   /hooks init

debug = false

[llm]
temperature = 0
timeout = 120.0

[llm.retry]
max_attempts = 5
min_wait = 2.0
max_wait = 60.0
multiplier = 1.0

[limits]
max_turns = 100
max_tool_output_tokens = 50000
max_file_size = 10485760
context_window = 128000
tool_output_display_tokens = 2000
tool_output_preview_tokens = 200
prune_protect_tokens = 10000
prune_minimum_tokens = 5000
compression_threshold = 0.8
"""


def _project_dir_name() -> str:
    return os.environ.get("PICHU_PROJECT_DIR", ".pichu")


def _config_file_name() -> str:
    return os.environ.get("PICHU_CONFIG_FILE", "config.toml")


def project_config_path(project_root: Path) -> Path:
    """Return the project-scoped config file path."""
    return project_root / _project_dir_name() / _config_file_name()


def ensure_core_project_scaffold(project_root: Path) -> list[str]:
    """Create only the core scaffold files needed for project bootstrapping."""
    created: list[str] = []
    config_dir = project_root / _project_dir_name()
    config_file = config_dir / _config_file_name()

    if not config_dir.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        created.append(f"{relative_to_project(config_dir, project_root)}/")

    if not config_file.exists():
        config_file.write_text(_CORE_PROJECT_CONFIG_TEMPLATE, encoding="utf-8")
        created.append(relative_to_project(config_file, project_root))

    return created


def ensure_gitignore_entry(project_root: Path, entry: str, comment: str | None = None) -> bool:
    """Append a gitignore entry if the file exists and the entry is missing."""
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        return False

    existing = gitignore.read_text(encoding="utf-8")
    existing_entries = {line.strip() for line in existing.splitlines() if line.strip()}
    if entry in existing_entries:
        return False

    with open(gitignore, "a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        if comment:
            handle.write(f"\n# {comment}\n")
        handle.write(f"{entry}\n")

    return True


def relative_to_project(path: Path, project_root: Path) -> str:
    """Return a stable project-relative path for user output."""
    try:
        return str(path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        return str(path)

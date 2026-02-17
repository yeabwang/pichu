"""Workspace trust persistence for startup safety prompts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from config import ensure_config_dir

TRUST_STORE_FILE = "trusted_workspaces.json"


def _normalize_workspace_path(workspace: Path | str) -> str:
    resolved = Path(workspace).expanduser().resolve()
    normalized = str(resolved)
    if os.name == "nt":
        normalized = normalized.lower()
    return normalized


def _store_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    return ensure_config_dir() / TRUST_STORE_FILE


def _load_trusted(path: Path) -> set[str]:
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    entries = payload.get("trusted_workspaces", [])
    if not isinstance(entries, list):
        return set()
    return {entry for entry in entries if isinstance(entry, str)}


def is_workspace_trusted(workspace: Path | str, store_path: Path | None = None) -> bool:
    normalized = _normalize_workspace_path(workspace)
    trusted = _load_trusted(_store_path(store_path))
    return normalized in trusted


def trust_workspace(workspace: Path | str, store_path: Path | None = None) -> bool:
    path = _store_path(store_path)
    trusted = _load_trusted(path)
    trusted.add(_normalize_workspace_path(workspace))

    payload = {"trusted_workspaces": sorted(trusted)}
    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
        temp_path.replace(path)
        return True
    except OSError:
        return False

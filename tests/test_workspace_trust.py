from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.workspace_trust import is_workspace_trusted, trust_workspace


def _normalized(path: Path) -> str:
    value = str(path.resolve())
    if os.name == "nt":
        return value.lower()
    return value


def test_workspace_trust_round_trip(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store_path = tmp_path / "trusted_workspaces.json"

    assert not is_workspace_trusted(workspace, store_path=store_path)
    assert trust_workspace(workspace, store_path=store_path)
    assert is_workspace_trusted(workspace, store_path=store_path)

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["trusted_workspaces"] == [_normalized(workspace)]


def test_workspace_trust_handles_corrupt_store_and_deduplicates(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store_path = tmp_path / "trusted_workspaces.json"
    store_path.write_text("{not-json", encoding="utf-8")

    assert not is_workspace_trusted(workspace, store_path=store_path)
    assert trust_workspace(workspace, store_path=store_path)
    assert trust_workspace(workspace, store_path=store_path)

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["trusted_workspaces"] == [_normalized(workspace)]

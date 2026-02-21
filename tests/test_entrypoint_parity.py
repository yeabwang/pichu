from __future__ import annotations

from pathlib import Path


def test_duplicate_src_entrypoint_no_longer_exists():
    repo_root = Path(__file__).resolve().parent.parent
    assert (repo_root / "main.py").exists()
    assert not (repo_root / "src" / "main.py").exists()

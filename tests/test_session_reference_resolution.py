"""Tests for session reference resolution semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.session_storage import (
    SessionReferenceAmbiguousError,
    SessionReferenceNotFoundError,
    SessionStorage,
)


def _make_storage(tmp_path: Path) -> SessionStorage:
    return SessionStorage(storage_dir=tmp_path / "sessions", cleanup_days=30, max_sessions=50)


def _create_session(
    storage: SessionStorage,
    session_id: str,
    title: str,
    cwd: Path,
) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    storage.create_session(session_id=session_id, cwd=str(cwd), model="test-model", title=title)


def test_resolve_session_reference_prefers_exact_id(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    cwd = tmp_path / "project"
    _create_session(storage, "abc-111", "Feature Alpha", cwd)

    match = storage.resolve_session_reference("abc-111", cwd=str(cwd))
    assert match.session_id == "abc-111"


def test_resolve_session_reference_supports_unique_prefix_and_title(
    tmp_path: Path,
) -> None:
    storage = _make_storage(tmp_path)
    cwd = tmp_path / "project"
    _create_session(storage, "alpha-111", "Auth Refactor", cwd)
    _create_session(storage, "beta-222", "Bug Bash", cwd)

    by_prefix = storage.resolve_session_reference("alpha-", cwd=str(cwd))
    assert by_prefix.session_id == "alpha-111"

    by_title = storage.resolve_session_reference("auth refactor", cwd=str(cwd))
    assert by_title.session_id == "alpha-111"


def test_resolve_session_reference_uses_cwd_scope(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    cwd_a = tmp_path / "project-a"
    cwd_b = tmp_path / "project-b"
    _create_session(storage, "id-a", "Shared Title", cwd_a)
    _create_session(storage, "id-b", "Shared Title", cwd_b)

    match = storage.resolve_session_reference("Shared Title", cwd=str(cwd_a))
    assert match.session_id == "id-a"


def test_resolve_session_reference_raises_on_ambiguous_match(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    cwd = tmp_path / "project"
    _create_session(storage, "same-prefix-a", "Alpha", cwd)
    _create_session(storage, "same-prefix-b", "Beta", cwd)

    with pytest.raises(SessionReferenceAmbiguousError):
        storage.resolve_session_reference("same-prefix", cwd=str(cwd))


def test_resolve_session_reference_raises_when_missing(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    cwd = tmp_path / "project"
    _create_session(storage, "real-session", "Existing Session", cwd)

    with pytest.raises(SessionReferenceNotFoundError):
        storage.resolve_session_reference("does-not-exist", cwd=str(cwd))

"""Session persistence layer.

Stores session transcripts and metadata to disk so conversations can be
resumed across process restarts.

Storage layout:
    ~/.pichu/sessions/
    ├── <session-id>/
    │   ├── metadata.json        # Session metadata
    │   └── transcript.jsonl     # Append-only message log
    └── <session-id>/
        └── ...

Design choices:
- JSONL transcript: append-only, crash-safe, one JSON object per line
- Metadata sidecar: lightweight JSON for index/listing without parsing full transcript
- Session per directory: easy cleanup, no single-file bottleneck
- 30-day auto-cleanup (configurable)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionMetadata:
    """Lightweight session metadata for indexing and display."""

    session_id: str
    title: str = ""
    created_at: str = ""  # ISO 8601
    updated_at: str = ""  # ISO 8601
    cwd: str = ""
    model: str = ""
    branch: str = ""
    turn_count: int = 0
    message_count: int = 0
    parent_session_id: str | None = None  # For forked sessions

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMetadata:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


class SessionReferenceError(ValueError):
    """Base error for user-provided session reference resolution failures."""


class SessionReferenceNotFoundError(SessionReferenceError):
    """Raised when no session matches the given reference."""

    def __init__(self, reference: str):
        self.reference = reference
        super().__init__(f"No session found matching '{reference}'.")


class SessionReferenceAmbiguousError(SessionReferenceError):
    """Raised when a reference matches multiple sessions."""

    def __init__(self, reference: str, matches: list[SessionMetadata]):
        self.reference = reference
        self.matches = matches
        sample = ", ".join(meta.session_id[:8] for meta in matches[:5])
        suffix = "..." if len(matches) > 5 else ""
        super().__init__(f"Session reference '{reference}' is ambiguous: {sample}{suffix}")


@dataclass
class TranscriptEntry:
    """A single line in the JSONL transcript."""

    role: str  # user | assistant | tool | system
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""  # ISO 8601
    turn: int = 0
    token_usage: dict[str, int] | None = None

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "role": self.role,
            "timestamp": self.timestamp,
            "turn": self.turn,
        }
        if self.content is not None:
            d["content"] = self.content
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.token_usage:
            d["token_usage"] = self.token_usage
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> TranscriptEntry:
        d = json.loads(line)
        return cls(
            role=d.get("role", ""),
            content=d.get("content"),
            tool_call_id=d.get("tool_call_id"),
            tool_calls=d.get("tool_calls", []),
            timestamp=d.get("timestamp", ""),
            turn=d.get("turn", 0),
            token_usage=d.get("token_usage"),
        )


def _get_git_branch(cwd: str) -> str:
    """Get the current git branch, or empty string."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


class SessionStorage:
    """Manages session persistence to disk.

    Thread-safety: NOT thread-safe. Callers must serialize access.
    """

    def __init__(self, storage_dir: Path, cleanup_days: int = 30, max_sessions: int = 100):
        self._storage_dir = Path(storage_dir).expanduser()
        self._cleanup_days = cleanup_days
        self._max_sessions = max_sessions
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    # Session lifecycle

    def create_session(
        self,
        session_id: str,
        cwd: str = "",
        model: str = "",
        title: str = "",
        parent_session_id: str | None = None,
    ) -> Path:
        """Create a new session directory and write initial metadata."""
        session_dir = self._storage_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now().isoformat()
        branch = _get_git_branch(cwd) if cwd else ""

        meta = SessionMetadata(
            session_id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
            cwd=cwd,
            model=model,
            branch=branch,
            parent_session_id=parent_session_id,
        )
        self._write_metadata(session_dir, meta)

        # Create empty transcript
        (session_dir / "transcript.jsonl").touch()

        return session_dir

    def session_exists(self, session_id: str) -> bool:
        return (self._storage_dir / session_id / "metadata.json").exists()

    def get_session_dir(self, session_id: str) -> Path:
        return self._storage_dir / session_id

    # Transcript (append-only JSONL)

    def append_message(self, session_id: str, entry: TranscriptEntry) -> None:
        """Append a single message to the transcript."""
        transcript = self._storage_dir / session_id / "transcript.jsonl"
        with open(transcript, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

    def load_transcript(self, session_id: str) -> list[TranscriptEntry]:
        """Load the full transcript from disk."""
        transcript = self._storage_dir / session_id / "transcript.jsonl"
        if not transcript.exists():
            return []
        entries: list[TranscriptEntry] = []
        with open(transcript, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(TranscriptEntry.from_json(line))
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Skipping corrupt transcript line {line_num}: {e}")
        return entries

    def truncate_transcript(self, session_id: str, keep_entries: int) -> None:
        """Truncate transcript to the first N entries (for rewind)."""
        entries = self.load_transcript(session_id)
        entries = entries[:keep_entries]
        transcript = self._storage_dir / session_id / "transcript.jsonl"
        with open(transcript, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(entry.to_json() + "\n")

    def copy_transcript(self, source_id: str, dest_id: str, max_entries: int | None = None) -> None:
        """Copy transcript from one session to another (for forking)."""
        entries = self.load_transcript(source_id)
        if max_entries is not None:
            entries = entries[:max_entries]
        transcript = self._storage_dir / dest_id / "transcript.jsonl"
        with open(transcript, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(entry.to_json() + "\n")

    # Metadata

    def get_metadata(self, session_id: str) -> SessionMetadata | None:
        """Load session metadata."""
        meta_path = self._storage_dir / session_id / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return SessionMetadata.from_dict(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read metadata for {session_id}: {e}")
            return None

    def update_metadata(self, session_id: str, **updates: Any) -> None:
        """Update specific fields in session metadata."""
        meta = self.get_metadata(session_id)
        if not meta:
            return
        for key, value in updates.items():
            if hasattr(meta, key):
                setattr(meta, key, value)
        meta.updated_at = datetime.now().isoformat()
        session_dir = self._storage_dir / session_id
        self._write_metadata(session_dir, meta)

    def _write_metadata(self, session_dir: Path, meta: SessionMetadata) -> None:
        meta_path = session_dir / "metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, indent=2, ensure_ascii=False)

    # Listing & search

    def list_sessions(
        self,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionMetadata]:
        """List sessions, most recent first.

        If *cwd* is given, only sessions from that directory are returned
        (sessions are tied to directories).
        """
        sessions: list[SessionMetadata] = []
        if not self._storage_dir.exists():
            return sessions

        for entry in self._storage_dir.iterdir():
            if not entry.is_dir():
                continue
            meta = self.get_metadata(entry.name)
            if meta is None:
                continue
            if cwd and meta.cwd and Path(meta.cwd).resolve() != Path(cwd).resolve():
                continue
            sessions.append(meta)

        # Sort by updated_at descending
        sessions.sort(key=lambda m: m.updated_at, reverse=True)
        return sessions[:limit]

    def find_most_recent(self, cwd: str | None = None) -> SessionMetadata | None:
        """Find the most recently updated session (for --continue)."""
        sessions = self.list_sessions(cwd=cwd, limit=1)
        return sessions[0] if sessions else None

    def find_by_title(self, title: str, cwd: str | None = None) -> SessionMetadata | None:
        """Find a session by title (case-insensitive partial match)."""
        title_lower = title.lower()
        for meta in self.list_sessions(cwd=cwd, limit=100):
            if title_lower in meta.title.lower():
                return meta
        return None

    def resolve_session_reference(
        self,
        reference: str,
        cwd: str | None = None,
        limit: int = 1000,
    ) -> SessionMetadata:
        """Resolve a user-facing session reference to one concrete session.

        Resolution order is intentionally deterministic:
        1) exact full session id
        2) unique session id prefix
        3) unique exact title match (case-insensitive)
        4) unique partial title match (case-insensitive)

        Raises:
            SessionReferenceNotFoundError: no matches for the reference
            SessionReferenceAmbiguousError: multiple matches at a given stage
        """
        query = reference.strip()
        if not query:
            raise SessionReferenceNotFoundError(reference)

        sessions = self.list_sessions(cwd=cwd, limit=limit)
        if not sessions:
            raise SessionReferenceNotFoundError(reference)

        exact_id = [m for m in sessions if m.session_id == query]
        if exact_id:
            return exact_id[0]

        prefix = [m for m in sessions if m.session_id.startswith(query)]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise SessionReferenceAmbiguousError(reference, prefix)

        query_folded = query.casefold()

        exact_title = [m for m in sessions if m.title and m.title.casefold() == query_folded]
        if len(exact_title) == 1:
            return exact_title[0]
        if len(exact_title) > 1:
            raise SessionReferenceAmbiguousError(reference, exact_title)

        partial_title = [m for m in sessions if m.title and query_folded in m.title.casefold()]
        if len(partial_title) == 1:
            return partial_title[0]
        if len(partial_title) > 1:
            raise SessionReferenceAmbiguousError(reference, partial_title)

        raise SessionReferenceNotFoundError(reference)

    # Cleanup

    def cleanup_old_sessions(self) -> int:
        """Remove sessions older than cleanup_days. Returns count removed."""
        if not self._storage_dir.exists():
            return 0

        cutoff = datetime.now().timestamp() - (self._cleanup_days * 86400)
        removed = 0

        for entry in list(self._storage_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta = self.get_metadata(entry.name)
            if meta is None:
                continue
            try:
                updated = datetime.fromisoformat(meta.updated_at).timestamp()
            except (ValueError, TypeError):
                updated = 0
            if updated < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1

        return removed

    def enforce_max_sessions(self) -> int:
        """Remove oldest sessions beyond max_sessions. Returns count removed."""
        sessions = self.list_sessions(limit=10000)
        if len(sessions) <= self._max_sessions:
            return 0

        to_remove = sessions[self._max_sessions :]
        removed = 0
        for meta in to_remove:
            session_dir = self._storage_dir / meta.session_id
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
                removed += 1

        return removed

    def delete_session(self, session_id: str) -> bool:
        """Delete a specific session. Returns True if deleted."""
        session_dir = self._storage_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
            return True
        return False

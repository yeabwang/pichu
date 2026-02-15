"""Checkpoint system for file snapshots and rewind.

Before every file edit made through the edit/write tools, the original
file content is captured. This enables:
- Restoring code to any previous turn
- Restoring conversation to any previous turn
- Restoring both code and conversation together
- "Summarize from here" (targeted compaction)

Design goals:
- Only edits through file editing tools are tracked (NOT shell commands)
- Each user prompt creates a new checkpoint group (keyed by turn number)
- Snapshots are idempotent per-turn (same file snapshotted once per turn)
- Checkpoints persist across sessions (stored in session directory)
- Cleaned up with the session (30-day default)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote
from urllib.parse import unquote as url_unquote

logger = logging.getLogger(__name__)


@dataclass
class FileSnapshot:
    """A snapshot of a file's content at a point in time."""

    file_path: str  # Absolute path
    content: str | None = None  # Original content (None = file didn't exist)
    timestamp: str = ""
    turn_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "had_content": self.content is not None,
            "timestamp": self.timestamp,
            "turn_number": self.turn_number,
        }


@dataclass
class Checkpoint:
    """A checkpoint representing the state at a user turn."""

    checkpoint_id: str  # "turn-{N}"
    turn_number: int
    user_message: str  # The user prompt that triggered this turn
    timestamp: str
    file_paths: list[str] = field(default_factory=list)  # Paths snapshotted
    message_index: int = 0  # Index into ContextManager._messages at turn start

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(
            checkpoint_id=d["checkpoint_id"],
            turn_number=d["turn_number"],
            user_message=d.get("user_message", ""),
            timestamp=d.get("timestamp", ""),
            file_paths=d.get("file_paths", []),
            message_index=d.get("message_index", 0),
        )


def _safe_filename(file_path: str) -> str:
    """URL-encode a file path to make it safe as a filename."""
    return url_quote(file_path, safe="")


def _unsafe_filename(filename: str) -> str:
    """Decode a URL-encoded filename back to a file path."""
    return url_unquote(filename)


class CheckpointManager:
    """Manages file checkpoints for rewind capability.

    Usage in the agent loop:
        1. begin_turn(turn, user_message, message_index)  — at start of each user turn
        2. snapshot_file(path)  — called by edit/write tools before modifying
        3. commit_turn()  — after the turn completes

    Rewind:
        - restore_code(to_turn)  — revert files to state at turn N
        - get_checkpoints()  — list all checkpoints for /rewind UI
    """

    def __init__(self, session_dir: Path):
        self._session_dir = Path(session_dir)
        self._checkpoints_dir = self._session_dir / "checkpoints"
        self._checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._checkpoints_dir / "manifest.json"

        # In-memory state
        self._checkpoints: list[Checkpoint] = []
        self._current_turn: int | None = None
        self._current_snapshots: dict[str, FileSnapshot] = {}  # path → snapshot
        self._current_message: str = ""
        self._current_message_index: int = 0

        # Load existing manifest
        self._load_manifest()

    # Turn lifecycle

    def begin_turn(self, turn: int, user_message: str, message_index: int) -> None:
        """Start tracking a new user turn."""
        self._current_turn = turn
        self._current_snapshots = {}
        self._current_message = user_message
        self._current_message_index = message_index

        # Create turn directory
        turn_dir = self._checkpoints_dir / f"turn-{turn:04d}"
        turn_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_file(self, file_path: str) -> None:
        """Capture a file's current content before editing.

        Idempotent per turn — if the same file is edited multiple times
        in one turn, only the first (pre-edit) state is captured.
        """
        if self._current_turn is None:
            return  # No active turn

        abs_path = str(Path(file_path).resolve())

        # Already snapshotted this file this turn
        if abs_path in self._current_snapshots:
            return

        # Read current content
        content: str | None = None
        path = Path(abs_path)
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                try:
                    content = path.read_text(encoding="latin-1")
                except OSError:
                    content = None

        snapshot = FileSnapshot(
            file_path=abs_path,
            content=content,
            timestamp=datetime.now().isoformat(),
            turn_number=self._current_turn,
        )
        self._current_snapshots[abs_path] = snapshot

        # Write snapshot to disk
        turn_dir = self._checkpoints_dir / f"turn-{self._current_turn:04d}"
        safe_name = _safe_filename(abs_path)
        snapshot_path = turn_dir / safe_name
        try:
            if content is not None:
                snapshot_path.write_text(content, encoding="utf-8")
            else:
                # Marker: file didn't exist
                snapshot_path.write_text("__CHECKPOINT_FILE_DID_NOT_EXIST__", encoding="utf-8")
        except OSError as e:
            logger.warning(f"Failed to write snapshot for {abs_path}: {e}")

    def commit_turn(self) -> None:
        """Finalize the current checkpoint and update manifest."""
        if self._current_turn is None:
            return

        checkpoint = Checkpoint(
            checkpoint_id=f"turn-{self._current_turn:04d}",
            turn_number=self._current_turn,
            user_message=self._current_message[:200],  # Truncate for display
            timestamp=datetime.now().isoformat(),
            file_paths=list(self._current_snapshots.keys()),
            message_index=self._current_message_index,
        )
        self._checkpoints.append(checkpoint)
        self._save_manifest()

        # Reset current turn state
        self._current_turn = None
        self._current_snapshots = {}
        self._current_message = ""

    # Rewind / Restore

    def get_checkpoints(self) -> list[Checkpoint]:
        """Get all checkpoints in chronological order."""
        return list(self._checkpoints)

    def get_checkpoint_at_turn(self, turn: int) -> Checkpoint | None:
        """Get the checkpoint for a specific turn."""
        for cp in self._checkpoints:
            if cp.turn_number == turn:
                return cp
        return None

    def restore_code(self, to_turn: int) -> list[str]:
        """Restore all files to their state at the given turn.

        Reverts ALL file changes made in turns > to_turn.
        Returns list of restored file paths.
        """
        restored: list[str] = []

        # Collect all unique files modified after the target turn
        files_to_restore: dict[str, int] = {}  # file_path → earliest turn after target
        for cp in self._checkpoints:
            if cp.turn_number > to_turn:
                for fp in cp.file_paths:
                    if fp not in files_to_restore:
                        files_to_restore[fp] = cp.turn_number

        # For each file, find the snapshot from the target turn or the
        # earliest snapshot that captured its state before modification
        for file_path in files_to_restore:
            content = self._find_content_at_turn(file_path, to_turn)
            try:
                path = Path(file_path)
                if content is None:
                    # File didn't exist at that point — delete it
                    if path.exists():
                        path.unlink()
                        restored.append(file_path)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                    restored.append(file_path)
            except OSError as e:
                logger.warning(f"Failed to restore {file_path}: {e}")

        return restored

    def _find_content_at_turn(self, file_path: str, target_turn: int) -> str | None:
        """Find the content of a file at a specific turn.

        Walks checkpoints to find the snapshot closest to (but <= ) the target turn.
        If no snapshot exists at or before target_turn, returns the earliest snapshot.
        """
        # Look for the snapshot at the earliest turn > target_turn
        # That snapshot contains the file's state BEFORE that turn's edit
        best_snapshot_turn: int | None = None
        for cp in self._checkpoints:
            if cp.turn_number > target_turn and file_path in cp.file_paths:
                if best_snapshot_turn is None or cp.turn_number < best_snapshot_turn:
                    best_snapshot_turn = cp.turn_number
                break  # Checkpoints are ordered, first match is earliest

        if best_snapshot_turn is not None:
            return self._read_snapshot_content(file_path, best_snapshot_turn)

        # If we have a snapshot AT or BEFORE the target turn, file wasn't
        # modified after that point — it's already in the correct state
        return self._read_current_content(file_path)

    def _read_snapshot_content(self, file_path: str, turn: int) -> str | None:
        """Read a snapshot file from disk."""
        safe_name = _safe_filename(file_path)
        snapshot_path = self._checkpoints_dir / f"turn-{turn:04d}" / safe_name
        if not snapshot_path.exists():
            return None
        try:
            content = snapshot_path.read_text(encoding="utf-8")
            if content == "__CHECKPOINT_FILE_DID_NOT_EXIST__":
                return None
            return content
        except OSError:
            return None

    def _read_current_content(self, file_path: str) -> str | None:
        """Read a file's current content from disk."""
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def get_message_index_at_turn(self, turn: int) -> int | None:
        """Get the message index at a specific turn (for conversation rewind)."""
        for cp in self._checkpoints:
            if cp.turn_number == turn:
                return cp.message_index
        return None

    # Persistence

    def _load_manifest(self) -> None:
        """Load checkpoint manifest from disk."""
        if not self._manifest_path.exists():
            return
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._checkpoints = [Checkpoint.from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"Failed to load checkpoint manifest: {e}")
            self._checkpoints = []

    def _save_manifest(self) -> None:
        """Save checkpoint manifest to disk."""
        try:
            with open(self._manifest_path, "w", encoding="utf-8") as f:
                json.dump([cp.to_dict() for cp in self._checkpoints], f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save checkpoint manifest: {e}")

    # Forking

    def copy_to(self, dest_session_dir: Path, up_to_turn: int | None = None) -> None:
        """Copy checkpoint data to another session directory (for forking)."""
        dest_cp_dir = dest_session_dir / "checkpoints"
        dest_cp_dir.mkdir(parents=True, exist_ok=True)

        checkpoints_to_copy = self._checkpoints
        if up_to_turn is not None:
            checkpoints_to_copy = [cp for cp in self._checkpoints if cp.turn_number <= up_to_turn]

        # Copy manifest
        dest_manifest = dest_cp_dir / "manifest.json"
        with open(dest_manifest, "w", encoding="utf-8") as f:
            json.dump([cp.to_dict() for cp in checkpoints_to_copy], f, indent=2)

        # Copy turn directories
        for cp in checkpoints_to_copy:
            src_turn_dir = self._checkpoints_dir / cp.checkpoint_id
            dest_turn_dir = dest_cp_dir / cp.checkpoint_id
            if src_turn_dir.exists():
                import shutil

                shutil.copytree(src_turn_dir, dest_turn_dir, dirs_exist_ok=True)

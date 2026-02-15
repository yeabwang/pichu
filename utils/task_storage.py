"""
Task Storage - File-based persistence for cross-session task coordination.

This module provides persistent storage for TaskLists with:
- Cross-session persistence (tasks survive restarts)
- File locking for multi-agent coordination
- Atomic writes to prevent corruption
- Support for shared task lists via environment variable

Storage location: ~/.pichu/tasks/<task-list-id>/
- tasks.json: Serialized task data with dependencies
- .lock: File lock for coordination between sessions

Reference: tasks are stored in ~/.pichu/tasks/
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.task_types import TaskList

logger = logging.getLogger(__name__)


class TaskStorageError(Exception):
    """Base exception for task storage operations."""

    pass


class TaskLockError(TaskStorageError):
    """Failed to acquire lock on task list."""

    pass


class TaskStorage:
    """
    File-based task storage with cross-session coordination.

    Features:
    - Persistent storage in ~/.pichu/tasks/
    - File locking for concurrent access
    - Atomic writes using temp files
    - Automatic directory creation

    Usage:
        storage = TaskStorage()

        # Save task list
        storage.save(task_list)

        # Load task list
        task_list = storage.load("my-task-list-id")

        # Delete task list
        storage.delete("my-task-list-id")
    """

    DEFAULT_BASE_DIR = "~/.pichu/tasks"
    TASKS_FILE = "tasks.json"
    LOCK_FILE = ".lock"
    DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
    DEFAULT_LOCK_POLL_INTERVAL_SECONDS = 0.1
    DEFAULT_STALE_LOCK_SECONDS = 300.0

    def __init__(self, base_dir: str | Path | None = None):
        """
        Initialize task storage.

        Args:
            base_dir: Base directory for task storage.
                      Defaults to ~/.pichu/tasks/
        """
        if base_dir is None:
            base_dir = os.environ.get("PICHU_TASKS_DIR", os.path.expanduser(self.DEFAULT_BASE_DIR))

        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Task storage initialized at {self.base_dir}")

    def _get_list_dir(self, list_id: str) -> Path:
        """Get directory path for a task list."""
        # Sanitize list_id to prevent path traversal
        safe_id = "".join(c for c in list_id if c.isalnum() or c in "-_")
        return self.base_dir / safe_id

    def _get_tasks_path(self, list_id: str) -> Path:
        """Get path to tasks.json for a list."""
        return self._get_list_dir(list_id) / self.TASKS_FILE

    def _get_lock_path(self, list_id: str) -> Path:
        """Get path to lock file for a list."""
        return self._get_list_dir(list_id) / self.LOCK_FILE

    def _is_stale_lock(self, lock_path: Path) -> bool:
        """Check if a lock file appears stale and safe to clear."""
        try:
            lock_age = time.time() - lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        return lock_age > self.DEFAULT_STALE_LOCK_SECONDS

    def _acquire_lock(
        self,
        list_id: str,
        timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> Path:
        """Acquire an exclusive lock file for a task list."""
        list_dir = self._get_list_dir(list_id)
        list_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._get_lock_path(list_id)

        deadline = time.monotonic() + timeout_seconds

        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                    lock_file.write(
                        json.dumps(
                            {
                                "pid": os.getpid(),
                                "acquired_at": datetime.now().isoformat(),
                            }
                        )
                    )
                return lock_path
            except FileExistsError:
                if self._is_stale_lock(lock_path):
                    try:
                        lock_path.unlink()
                        logger.warning(f"Removed stale task lock: {lock_path}")
                        continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass

                if time.monotonic() >= deadline:
                    raise TaskLockError(f"Timed out acquiring lock for task list {list_id}")

                time.sleep(self.DEFAULT_LOCK_POLL_INTERVAL_SECONDS)

    @contextmanager
    def acquire_lock(
        self,
        list_id: str,
        timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ):
        """Context manager for exclusive task list operations."""
        lock_path = self._acquire_lock(list_id, timeout_seconds=timeout_seconds)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def save(self, task_list: TaskList) -> None:
        """
        Save task list to disk atomically.

        Uses temp file + rename for atomic writes to prevent
        corruption from interrupted writes.

        Args:
            task_list: TaskList to save

        Raises:
            TaskStorageError: If save fails
        """
        list_dir = self._get_list_dir(task_list.id)
        list_dir.mkdir(parents=True, exist_ok=True)

        tasks_path = self._get_tasks_path(task_list.id)

        try:
            # Serialize to dict
            data = task_list.to_dict()

            # Add storage metadata
            data["_storage"] = {
                "version": 1,
                "saved_at": datetime.now().isoformat(),
            }

            # Atomic write using temp file
            temp_fd, temp_path = tempfile.mkstemp(dir=list_dir, prefix="tasks_", suffix=".tmp")

            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                # Atomic rename
                shutil.move(temp_path, tasks_path)
                logger.debug(f"Saved task list {task_list.id} ({len(task_list)} tasks)")

            except Exception:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

        except Exception as e:
            logger.error(f"Failed to save task list {task_list.id}: {e}")
            raise TaskStorageError(f"Failed to save task list: {e}") from e

    def load(self, list_id: str) -> TaskList | None:
        """
        Load task list from disk.

        Args:
            list_id: ID of the task list to load

        Returns:
            TaskList if found, None otherwise

        Raises:
            TaskStorageError: If load fails (corrupted data, etc.)
        """
        tasks_path = self._get_tasks_path(list_id)

        if not tasks_path.exists():
            logger.debug(f"Task list {list_id} not found")
            return None

        try:
            with open(tasks_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Remove storage metadata before deserialization
            data.pop("_storage", None)

            task_list = TaskList.from_dict(data)
            logger.debug(f"Loaded task list {list_id} ({len(task_list)} tasks)")
            return task_list

        except json.JSONDecodeError as e:
            logger.error(f"Corrupted task list {list_id}: {e}")
            raise TaskStorageError(f"Corrupted task list data: {e}") from e
        except Exception as e:
            logger.error(f"Failed to load task list {list_id}: {e}")
            raise TaskStorageError(f"Failed to load task list: {e}") from e

    def delete(self, list_id: str) -> bool:
        """
        Delete a task list from storage.

        Args:
            list_id: ID of the task list to delete

        Returns:
            True if deleted, False if not found
        """
        list_dir = self._get_list_dir(list_id)

        if not list_dir.exists():
            return False

        try:
            shutil.rmtree(list_dir)
            logger.debug(f"Deleted task list {list_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete task list {list_id}: {e}")
            raise TaskStorageError(f"Failed to delete task list: {e}") from e

    def exists(self, list_id: str) -> bool:
        """Check if a task list exists in storage."""
        return self._get_tasks_path(list_id).exists()

    def list_all(self) -> list[str]:
        """
        List all task list IDs in storage.

        Returns:
            List of task list IDs
        """
        if not self.base_dir.exists():
            return []

        list_ids = []
        for item in self.base_dir.iterdir():
            if item.is_dir() and (item / self.TASKS_FILE).exists():
                list_ids.append(item.name)

        return sorted(list_ids)

    def get_summary(self, list_id: str) -> dict[str, Any] | None:
        """
        Get summary statistics for a task list without loading all data.

        Returns:
            Dict with id, total, completed, updated_at or None if not found
        """
        task_list = self.load(list_id)
        if task_list is None:
            return None

        stats = task_list.get_stats()
        return {
            "id": task_list.id,
            "total": stats["total"],
            "completed": stats["completed"],
            "in_progress": stats["in_progress"],
            "available": stats["available"],
            "blocked": stats["blocked"],
            "updated_at": task_list.updated_at.isoformat(),
        }


# Global storage instance (lazy initialized)
_storage: TaskStorage | None = None


def get_task_storage() -> TaskStorage:
    """Get the global task storage instance."""
    global _storage
    if _storage is None:
        _storage = TaskStorage()
    return _storage


def reset_task_storage() -> None:
    """Reset the global task storage (for testing)."""
    global _storage
    _storage = None

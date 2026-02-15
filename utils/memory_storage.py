"""
Memory Storage - File-based persistence for the memory system.

This module handles saving and loading memories to disk:
- Atomic writes (temp file + rename) for data safety
- JSON serialization with datetime handling
- Separate stores for global and project-specific memories
- Auto-migration and schema versioning

Storage locations:
- Global: ~/.pichu/memory/state.json
- Project: <project>/.pichu/memory/state.json
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from utils.memory_types import Memory, MemoryCategory, MemoryStore

logger = logging.getLogger(__name__)

# Schema version for future migrations
SCHEMA_VERSION: Final[int] = 1
STATE_FILE_NAME: Final[str] = "state.json"
BACKUP_FILE_NAME: Final[str] = "state.backup.json"
CURSOR_FILE_NAME: Final[str] = "cursor.json"
MEMORY_START_MARKER: Final[str] = "<!-- MEMORY:START -->"
MEMORY_END_MARKER: Final[str] = "<!-- MEMORY:END -->"


class MemoryStorage:
    """
    File-based storage for memories.

    Handles persistence to JSON files with atomic writes,
    automatic backup, and schema versioning.
    """

    def __init__(
        self,
        storage_path: Path,
        project: str | None = None,
        auto_create: bool = True,
    ):
        """
        Initialize memory storage.

        Args:
            storage_path: Directory for storing memory files
            project: Project identifier (for project-specific stores)
            auto_create: Create storage directory if it doesn't exist
        """
        self._storage_path = Path(storage_path)
        self._project = project

        if auto_create:
            self._storage_path.mkdir(parents=True, exist_ok=True)

        self._state_file = self._storage_path / STATE_FILE_NAME
        self._backup_file = self._storage_path / BACKUP_FILE_NAME
        self._cursor_file = self._storage_path / CURSOR_FILE_NAME

    @property
    def storage_path(self) -> Path:
        """Get storage directory path."""
        return self._storage_path

    @property
    def exists(self) -> bool:
        """Check if storage file exists."""
        return self._state_file.exists()

    def _empty_store(self) -> MemoryStore:
        """Create a new empty store for this storage scope."""
        return MemoryStore(project=self._project)

    def load(self) -> MemoryStore:
        """
        Load memory store from disk.

        Returns empty store if file doesn't exist.
        Attempts to recover from backup if main file is corrupted.
        """
        if not self._state_file.exists():
            logger.debug(f"No state file found at {self._state_file}, creating new store")
            return self._empty_store()

        try:
            data = self._read_json(self._state_file)
            store = self._deserialize(data)
            self._apply_lifecycle_maintenance(store)
            return store

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to load state file: {e}, attempting backup recovery")
            return self._recover_from_backup()

    def save(self, store: MemoryStore) -> None:
        """
        Save memory store to disk.

        Uses atomic write (temp file + rename) for data safety.
        Creates backup of previous state.
        """
        if self._state_file.exists():
            self._create_backup()

        data = self._serialize(store)
        self._write_json(self._state_file, data)

        logger.debug(f"Saved {store.count} memories to {self._state_file}")

    def delete(self) -> bool:
        """
        Delete all memory data.

        Returns True if deleted, False if nothing to delete.
        """
        deleted = False
        for path in (self._state_file, self._backup_file, self._cursor_file):
            if self._delete_file(path):
                deleted = True
        return deleted

    def get_cursor(self) -> dict[str, Any]:
        """
        Get processing cursor for tracking what's been processed.

        Used to track conversation processing position.
        """
        if not self._cursor_file.exists():
            return self._default_cursor()

        try:
            raw_cursor = self._read_json(self._cursor_file)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Invalid cursor file detected at %s", self._cursor_file)
            return self._default_cursor()
        return self._normalize_cursor(raw_cursor)

    def set_cursor(self, cursor: dict[str, Any]) -> None:
        """Update processing cursor."""
        updated_cursor = dict(cursor)
        updated_cursor["updated_at"] = datetime.now().isoformat()
        self._write_json(self._cursor_file, self._normalize_cursor(updated_cursor))

    def _apply_lifecycle_maintenance(self, store: MemoryStore) -> None:
        """Apply decay/pruning maintenance and persist when changes occur."""
        store.decay_all()
        pruned = store.prune_expired()
        if pruned <= 0:
            return
        logger.debug(f"Pruned {pruned} expired memories on load")
        self.save(store)

    def _create_backup(self) -> None:
        """Create backup of the current state file."""
        try:
            shutil.copy2(self._state_file, self._backup_file)
        except OSError as e:
            logger.warning(f"Failed to create backup: {e}")

    def _delete_file(self, path: Path) -> bool:
        """Delete one storage file if present."""
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete %s: %s", path, e)
            return False

    def _default_cursor(self) -> dict[str, Any]:
        """Default cursor payload when none exists or data is invalid."""
        return {"last_processed": None, "position": 0}

    def _normalize_cursor(self, cursor: Any) -> dict[str, Any]:
        """Normalize cursor structure while preserving unknown keys."""
        if not isinstance(cursor, dict):
            return self._default_cursor()

        normalized = dict(cursor)
        normalized.setdefault("last_processed", None)

        position = normalized.get("position", 0)
        try:
            position = int(position)
        except (TypeError, ValueError):
            position = 0
        normalized["position"] = max(position, 0)

        updated_at = normalized.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            normalized["updated_at"] = str(updated_at)

        return normalized

    def _serialize(self, store: MemoryStore) -> dict[str, Any]:
        """Serialize store to dictionary with schema version."""
        data = store.to_dict()
        data["_schema_version"] = SCHEMA_VERSION
        data["_serialized_at"] = datetime.now().isoformat()
        return data

    def _deserialize(self, data: dict[str, Any]) -> MemoryStore:
        """Deserialize store from dictionary with migration support."""
        if not isinstance(data, dict):
            raise ValueError("State payload must be a JSON object")

        schema_version = data.get("_schema_version", 0)
        if not isinstance(schema_version, int):
            raise ValueError(f"Invalid schema version type: {type(schema_version).__name__}")

        # Apply migrations if needed
        if schema_version < SCHEMA_VERSION:
            data = self._migrate(dict(data), schema_version)

        return MemoryStore.from_dict(data)

    def _migrate(self, data: dict[str, Any], from_version: int) -> dict[str, Any]:
        """Apply schema migrations."""
        # Version 0 -> 1: Add confidence field
        if from_version < 1:
            memories = data.get("memories", [])
            if not isinstance(memories, list):
                raise ValueError("Invalid memories payload during migration")
            for memory in memories:
                if isinstance(memory, dict) and "confidence" not in memory:
                    memory["confidence"] = 1.0

        data["_schema_version"] = SCHEMA_VERSION
        return data

    def _recover_from_backup(self) -> MemoryStore:
        """Attempt to recover from backup file."""
        if not self._backup_file.exists():
            logger.warning("No backup file found, returning empty store")
            return self._empty_store()

        try:
            data = self._read_json(self._backup_file)
            store = self._deserialize(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.error(f"Backup recovery failed: {e}, returning empty store")
            return self._empty_store()

        try:
            self._write_json(self._state_file, self._serialize(store))
        except OSError as e:
            logger.warning("Recovered memory store but failed to rewrite state file: %s", e)

        logger.info(f"Recovered {store.count} memories from backup")
        return store

    def _read_json(self, path: Path) -> dict[str, Any]:
        """Read JSON file with encoding handling."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        """
        Write JSON file atomically.

        Uses temp file + rename for atomic write on POSIX systems.
        Falls back to direct write on Windows if rename fails.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="memory_",
            dir=path.parent,
        )
        temp_path_obj = Path(temp_path)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())

            try:
                temp_path_obj.replace(path)
            except OSError:
                shutil.copy2(temp_path_obj, path)
        finally:
            try:
                temp_path_obj.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Failed to clean up temp file %s: %s", temp_path_obj, e)

    def export_markdown(self, store: MemoryStore, output_path: Path) -> None:
        """Export memory store as markdown file."""
        markdown = store.to_markdown()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")

        logger.debug(f"Exported memories to {output_path}")

    def import_markdown(self, input_path: Path) -> list[Memory]:
        """
        Import memories from markdown file.

        Parses markdown and extracts memories from list items.
        Returns list of imported memories (not yet added to store).
        """
        if not input_path.exists():
            return []

        file_content = input_path.read_text(encoding="utf-8")

        memories: list[Memory] = []
        current_category = MemoryCategory.CONTEXT

        for line in file_content.splitlines():
            line = line.strip()

            # Skip empty lines and markers
            if not line or line.startswith("<!--") or line.startswith("_"):
                continue

            # Detect category headers
            if line.startswith("## "):
                current_category = self._category_from_header(line[3:], default=current_category)
                continue

            # Parse list items as memories
            if line.startswith("- "):
                memory_content = line[2:].strip()
                if memory_content:
                    memory = Memory(
                        content=memory_content,
                        category=current_category,
                        source="markdown_import",
                    )
                    memories.append(memory)

        logger.debug(f"Imported {len(memories)} memories from {input_path}")
        return memories

    def _category_from_header(self, header: str, default: MemoryCategory = MemoryCategory.CONTEXT) -> MemoryCategory:
        """Map markdown section headers to memory categories."""
        header_lower = header.lower()
        category_rules: tuple[tuple[tuple[str, ...], MemoryCategory], ...] = (
            (("architecture",), MemoryCategory.ARCHITECTURE),
            (("decision",), MemoryCategory.DECISION),
            (("pattern", "convention"), MemoryCategory.PATTERN),
            (("gotcha", "pitfall"), MemoryCategory.GOTCHA),
            (("progress",), MemoryCategory.PROGRESS),
            (("context",), MemoryCategory.CONTEXT),
            (("preference", "user"), MemoryCategory.USER_PREFERENCE),
        )

        for keywords, category in category_rules:
            if any(keyword in header_lower for keyword in keywords):
                return category

        return default

    def get_statistics(self) -> dict[str, Any]:
        """Get storage statistics."""
        stats = {
            "storage_path": str(self._storage_path),
            "state_file_exists": self._state_file.exists(),
            "backup_exists": self._backup_file.exists(),
            "cursor_file_exists": self._cursor_file.exists(),
        }

        if self._state_file.exists():
            stat = self._state_file.stat()
            stats["state_file_size"] = stat.st_size
            stats["state_file_modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

        return stats


class GlobalMemoryStorage(MemoryStorage):
    """
    Global memory storage in user's home directory.

    Stores user preferences and cross-project memories.
    Location: Configurable, default ~/.pichu/memory/
    """

    def __init__(self, storage_path: Path | None = None):
        """
        Initialize global memory storage.

        Args:
            storage_path: Custom storage path (from config), or None for default
        """
        if storage_path is None:
            storage_path = Path.home() / ".pichu" / "memory"
        super().__init__(storage_path, project=None)


class ProjectMemoryStorage(MemoryStorage):
    """
    Project-specific memory storage.

    Stores project-level memories in the project directory.
    Location: Configurable, default <project>/.pichu/memory/
    """

    def __init__(
        self,
        project_root: Path,
        project_name: str | None = None,
        memory_dir: str = ".pichu/memory",
        agents_file: str = "AGENTS.md",
        local_agents_file: str = "AGENTS.local.md",
    ):
        """
        Initialize project memory storage.

        Args:
            project_root: Root directory of the project
            project_name: Optional project name override
            memory_dir: Relative path for memory storage (from config)
            agents_file: Name of project AGENTS.md file
            local_agents_file: Name of local (gitignored) AGENTS.md file
        """
        storage_path = Path(project_root) / memory_dir
        project = project_name or Path(project_root).name
        super().__init__(storage_path, project=project)
        self._project_root = Path(project_root)
        self._agents_file = agents_file
        self._local_agents_file = local_agents_file

    @property
    def project_root(self) -> Path:
        """Get project root directory."""
        return self._project_root

    def get_agents_md_path(self) -> Path:
        """Get path to project's AGENTS.md file."""
        return self._project_root / self._agents_file

    def get_agents_local_md_path(self) -> Path:
        """Get path to project's AGENTS.local.md file."""
        return self._project_root / self._local_agents_file

    def sync_to_agents_md(self, store: MemoryStore) -> None:
        """
        Sync memories to AGENTS.md file.

        Updates the memory block in AGENTS.md while preserving
        other content in the file.
        """
        agents_path = self.get_agents_md_path()
        memory_markdown = store.to_markdown()

        if agents_path.exists():
            existing_content = agents_path.read_text(encoding="utf-8")
            new_content = self._upsert_memory_block(existing_content, memory_markdown)
        else:
            new_content = f"# {store.project or 'Project'}\n\n{memory_markdown}"

        agents_path.write_text(new_content, encoding="utf-8")
        logger.debug(f"Synced memories to {agents_path}")

    def _upsert_memory_block(self, content: str, memory_markdown: str) -> str:
        """Replace existing memory block in AGENTS.md content, or append if absent."""
        start_idx = content.find(MEMORY_START_MARKER)
        end_idx = content.find(MEMORY_END_MARKER)

        if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
            return content.rstrip() + "\n\n" + memory_markdown

        end_idx += len(MEMORY_END_MARKER)
        prefix = content[:start_idx].rstrip()
        suffix = content[end_idx:].lstrip("\n")

        if prefix and suffix:
            return f"{prefix}\n\n{memory_markdown}\n\n{suffix}"
        if prefix:
            return f"{prefix}\n\n{memory_markdown}"
        if suffix:
            return f"{memory_markdown}\n\n{suffix}"
        return memory_markdown

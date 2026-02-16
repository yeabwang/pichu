"""
Memory Manager - High-level coordinator for the memory system.

This is the main interface for memory operations:
- Manages both global and project-specific memories
- Coordinates storage, search, and lifecycle operations
- Handles memory deduplication and decay
- Provides unified access across memory tiers

Architecture:
    MemoryManager
        ├── GlobalMemoryStorage  (configurable, default ~/.pichu/memory/)
        ├── ProjectMemoryStorage (configurable, default <project>/.pichu/memory/)
        └── AgentsLoader (AGENTS.md hierarchy)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from utils.memory_storage import (
    GlobalMemoryStorage,
    MemoryStorage,
    ProjectMemoryStorage,
)
from utils.memory_types import Memory, MemoryCategory, MemoryStore

if TYPE_CHECKING:
    from config.config import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    High-level memory coordinator.

    Provides a unified interface for memory operations across
    global and project-specific stores.
    """

    def __init__(
        self,
        project_root: Path | str | None = None,
        project_name: str | None = None,
        config: "MemoryConfig | None" = None,
        auto_load: bool = True,
    ):
        """
        Initialize memory manager.

        Args:
            project_root: Path to project root (for project memories)
            project_name: Optional project name override
            config: Memory configuration (paths, behavior settings)
            auto_load: Load memories from disk on init
        """
        self._project_root = Path(project_root) if project_root else None
        self._project_name = project_name
        self._config = config

        # Get paths from config or use defaults
        global_path = None
        project_memory_dir = ".pichu/memory"
        agents_file = "AGENTS.md"
        local_agents_file = "AGENTS.local.md"

        if config:
            global_path = config.global_memory_path
            project_memory_dir = config.project_memory_dir
            agents_file = config.project_agents_file
            local_agents_file = config.local_agents_file
            auto_load = config.auto_load if auto_load else False

        # Initialize storages with config paths
        self._global_storage = GlobalMemoryStorage(storage_path=global_path)
        self._project_storage: ProjectMemoryStorage | None = None

        if self._project_root:
            self._project_storage = ProjectMemoryStorage(
                self._project_root,
                project_name=self._project_name,
                memory_dir=project_memory_dir,
                agents_file=agents_file,
                local_agents_file=local_agents_file,
            )

        # Memory stores (lazy loaded)
        self._global_store: MemoryStore | None = None
        self._project_store: MemoryStore | None = None

        # Event callbacks
        self._on_memory_added: list[Callable[[Memory], None]] = []
        self._on_memory_updated: list[Callable[[Memory], None]] = []
        self._on_memory_removed: list[Callable[[str], None]] = []

        if auto_load:
            self._load_stores()

    @property
    def project_root(self) -> Path | None:
        """Get current project root."""
        return self._project_root

    @property
    def project_name(self) -> str | None:
        """Get current project name."""
        if self._project_store:
            return self._project_store.project
        return self._project_name

    @property
    def global_store(self) -> MemoryStore:
        """Get global memory store."""
        if self._global_store is None:
            self._global_store = self._global_storage.load()
        return self._global_store

    @property
    def project_store(self) -> MemoryStore | None:
        """Get project memory store."""
        if self._project_storage is None:
            return None
        if self._project_store is None:
            self._project_store = self._project_storage.load()
        return self._project_store

    def _load_stores(self) -> None:
        """Load all memory stores from disk."""
        self._global_store = self._global_storage.load()

        if self._project_storage:
            self._project_store = self._project_storage.load()

    def _normalize_category(self, category: MemoryCategory | str | None) -> MemoryCategory | None:
        """Normalize category input to MemoryCategory."""
        if isinstance(category, str):
            return MemoryCategory.from_string(category)
        return category

    def _resolve_store(self, is_global: bool) -> tuple[MemoryStore, MemoryStorage]:
        """Resolve target store/storage pair for save operations."""
        if is_global:
            return self.global_store, self._global_storage

        project_store = self.project_store
        if project_store is not None and self._project_storage is not None:
            return project_store, self._project_storage

        return self.global_store, self._global_storage

    def _notify_added(self, memory: Memory) -> None:
        for callback in self._on_memory_added:
            callback(memory)

    def _notify_updated(self, memory: Memory) -> None:
        for callback in self._on_memory_updated:
            callback(memory)

    # Memory Operations

    def save(
        self,
        content: str,
        category: MemoryCategory | str = MemoryCategory.CONTEXT,
        tags: list[str] | None = None,
        confidence: float = 1.0,
        source: str = "user",
        is_global: bool = False,
    ) -> Memory:
        """
        Save a new memory.

        Args:
            content: The memory content to save
            category: Memory category (affects retention)
            tags: Optional tags for search
            confidence: Initial confidence score (0-1)
            source: Source of the memory
            is_global: Store in global (cross-project) memory

        Returns:
            The created or updated memory
        """
        resolved_category = self._normalize_category(category) or MemoryCategory.CONTEXT

        # Create memory
        memory = Memory(
            content=content,
            category=resolved_category,
            tags=tags or [],
            confidence=confidence,
            source=source,
        )

        store, storage = self._resolve_store(is_global=is_global)
        stored_memory, is_new = store.add_with_status(memory)

        storage.save(store)

        if is_new:
            self._notify_added(stored_memory)
        else:
            self._notify_updated(stored_memory)

        logger.debug(
            f"{'Saved' if is_new else 'Updated'} memory: "
            f"{stored_memory.content[:50]}... [{stored_memory.category.value}]"
        )

        return stored_memory

    def search(
        self,
        query: str | None = None,
        category: MemoryCategory | str | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.3,
        limit: int = 20,
        include_global: bool = True,
    ) -> list[Memory]:
        """
        Search memories across stores.

        Args:
            query: Text to search in content
            category: Filter by category
            tags: Filter by tags (any match)
            min_confidence: Minimum confidence threshold
            limit: Maximum results to return
            include_global: Include global memories

        Returns:
            List of matching memories, sorted by relevance
        """
        results: list[Memory] = []
        normalized_category = self._normalize_category(category)

        # Search project store first (higher priority)
        if self.project_store:
            project_results = self.project_store.search(
                query=query,
                category=normalized_category,
                tags=tags,
                min_confidence=min_confidence,
            )
            results.extend(project_results)

        # Search global store
        if include_global:
            global_results = self.global_store.search(
                query=query,
                category=normalized_category,
                tags=tags,
                min_confidence=min_confidence,
            )
            results.extend(global_results)

        # Sort by confidence (project memories already have higher base)
        results.sort(key=lambda m: m.confidence, reverse=True)

        return results[:limit]

    def view(
        self,
        category: MemoryCategory | str | None = None,
        include_global: bool = True,
    ) -> dict[str, list[Memory]]:
        """
        View all memories grouped by category.

        Returns:
            Dictionary mapping category names to memory lists
        """
        result: dict[str, list[Memory]] = {}
        normalized_category = self._normalize_category(category)

        # Get project memories
        if self.project_store:
            for cat, memories in self.project_store.by_category().items():
                if normalized_category is None or cat == normalized_category:
                    key = f"{cat.value} (project)"
                    result[key] = memories

        # Get global memories
        if include_global:
            for cat, memories in self.global_store.by_category().items():
                if normalized_category is None or cat == normalized_category:
                    key = f"{cat.value} (global)"
                    if key in result:
                        result[key].extend(memories)
                    else:
                        result[key] = memories

        return result

    def forget(
        self,
        memory_id: str | None = None,
        query: str | None = None,
        category: MemoryCategory | str | None = None,
    ) -> int:
        """
        Remove memories.

        Args:
            memory_id: Specific memory ID to remove
            query: Remove memories matching query
            category: Remove all memories in category

        Returns:
            Number of memories removed
        """
        removed = 0
        normalized_category = self._normalize_category(category)

        # Remove by ID
        if memory_id:
            if self.project_store and self.project_store.remove(memory_id):
                removed += 1
                if self._project_storage:
                    self._project_storage.save(self.project_store)
            if self.global_store.remove(memory_id):
                removed += 1
                self._global_storage.save(self.global_store)

        # Remove by query
        elif query:
            for store, storage in self._get_stores_with_storage():
                store_removed = 0
                matches = store.search(query=query, min_confidence=0.0)
                for memory in matches:
                    if store.remove(memory.id):
                        store_removed += 1
                        removed += 1
                if store_removed > 0:
                    storage.save(store)

        # Remove by category
        elif normalized_category:
            for store, storage in self._get_stores_with_storage():
                store_removed = 0
                memories = store.by_category().get(normalized_category, [])
                for memory in memories:
                    if store.remove(memory.id):
                        store_removed += 1
                        removed += 1
                if store_removed > 0:
                    storage.save(store)

        # Notify listeners
        if memory_id and removed > 0:
            for callback in self._on_memory_removed:
                callback(memory_id)

        logger.debug(f"Forgot {removed} memories")
        return removed

    def _get_stores_with_storage(self) -> list[tuple[MemoryStore, MemoryStorage]]:
        """Get all active stores with their storage backends."""
        stores: list[tuple[MemoryStore, MemoryStorage]] = []

        if self.project_store and self._project_storage:
            stores.append((self.project_store, self._project_storage))

        stores.append((self.global_store, self._global_storage))

        return stores

    # Lifecycle Operations

    def decay_all(self) -> None:
        """Apply decay to all memories based on their categories."""
        if self.project_store and self._project_storage:
            self.project_store.decay_all()
            self._project_storage.save(self.project_store)

        self.global_store.decay_all()
        self._global_storage.save(self.global_store)

    def prune_expired(self) -> int:
        """Remove all expired memories."""
        pruned = 0

        if self.project_store and self._project_storage:
            pruned += self.project_store.prune_expired()
            self._project_storage.save(self.project_store)

        pruned += self.global_store.prune_expired()
        self._global_storage.save(self.global_store)

        return pruned

    def reload(self) -> None:
        """Reload all memory stores from disk."""
        self._global_store = None
        self._project_store = None
        self._load_stores()

    # AGENTS.md Integration

    def sync_to_agents_md(self) -> None:
        """Sync project memories to AGENTS.md file."""
        if self._project_storage and self.project_store:
            self._project_storage.sync_to_agents_md(self.project_store)

    def import_from_agents_md(self) -> int:
        """
        Import memories from existing AGENTS.md file.

        Returns number of imported memories.
        """
        if not self._project_storage:
            return 0

        agents_path = self._project_storage.get_agents_md_path()
        imported_memories = self._project_storage.import_markdown(agents_path)

        store = self.project_store
        if store is None:
            return 0

        count = 0
        for memory in imported_memories:
            _, is_new = store.add_with_status(memory)
            if is_new:
                count += 1

        if count > 0:
            self._project_storage.save(store)

        return count

    # Context Building

    def get_context_block(
        self,
        max_memories: int = 30,
        min_confidence: float = 0.4,
    ) -> str:
        """
        Build context block for LLM prompt injection.

        Returns markdown-formatted memory context.
        """
        lines = ["## 🧠 Memory Context\n"]

        # Get high-priority memories
        all_memories = self.search(
            min_confidence=min_confidence,
            limit=max_memories,
            include_global=True,
        )

        if not all_memories:
            return ""

        # Group by category
        by_category: dict[MemoryCategory, list[Memory]] = {}
        for memory in all_memories:
            by_category.setdefault(memory.category, []).append(memory)

        # Format each category
        for category in MemoryCategory:
            if category not in by_category:
                continue

            memories = by_category[category]
            lines.append(f"\n### {category.emoji} {category.value.title()}\n")

            for memory in memories:
                conf_indicator = self._confidence_indicator(memory.confidence)
                lines.append(f"- {conf_indicator} {memory.content}")

        lines.append("\n---\n")
        return "\n".join(lines)

    def _confidence_indicator(self, confidence: float) -> str:
        """Get visual indicator for confidence level."""
        if confidence >= 0.9:
            return "●"  # High confidence
        elif confidence >= 0.7:
            return "◐"  # Medium confidence
        elif confidence >= 0.5:
            return "○"  # Lower confidence
        else:
            return "◌"  # Low confidence (may decay soon)

    # Statistics & Debug

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive memory statistics."""
        stats: dict[str, Any] = {
            "global": {
                "count": self.global_store.count,
                "by_category": {cat.value: len(mems) for cat, mems in self.global_store.by_category().items()},
                "storage": self._global_storage.get_statistics(),
            }
        }

        if self.project_store and self._project_storage:
            stats["project"] = {
                "name": self.project_name,
                "count": self.project_store.count,
                "by_category": {cat.value: len(mems) for cat, mems in self.project_store.by_category().items()},
                "storage": self._project_storage.get_statistics(),
            }

        global_count: int = stats["global"]["count"]
        project_count: int = stats.get("project", {}).get("count", 0)
        stats["total"] = global_count + project_count

        return stats

    # Event Registration

    def on_memory_added(self, callback: Callable[[Memory], None]) -> None:
        """Register callback for memory added events."""
        self._on_memory_added.append(callback)

    def on_memory_updated(self, callback: Callable[[Memory], None]) -> None:
        """Register callback for memory updated events."""
        self._on_memory_updated.append(callback)

    def on_memory_removed(self, callback: Callable[[str], None]) -> None:
        """Register callback for memory removed events."""
        self._on_memory_removed.append(callback)

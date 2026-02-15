"""
Memory Types - Core data structures for the memory system.

This module defines the fundamental types for Pichu's persistent memory:
- Memory: Individual memory items with metadata
- MemoryCategory: Classification of memory types
- MemoryStore: Collection of memories with search capabilities

The memory system is inspired by practical persistent-memory patterns,
providing context across sessions.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Final

DEFAULT_EXPIRY_DAYS: Final[dict["MemoryCategory", int | None]] = {}
DECAY_RATES: Final[dict["MemoryCategory", float]] = {}
CATEGORY_PRIORITIES: Final[dict["MemoryCategory", int]] = {}
CATEGORY_EMOJIS: Final[dict["MemoryCategory", str]] = {}
CATEGORY_HEADERS: Final[dict["MemoryCategory", str]] = {}
SIMILARITY_DEDUP_THRESHOLD: Final[float] = 0.8
MAX_MEMORIES_PER_CATEGORY_IN_MARKDOWN: Final[int] = 10


def _normalize_tags(tags: Any) -> list[str]:
    """Normalize tags to non-empty unique strings while preserving order."""
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [tags]
    elif not isinstance(tags, (list, tuple, set)):
        tags = [tags]
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = str(tag).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    """Best-effort parse for datetime fields."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return fallback
    return fallback


def _parse_optional_datetime(value: Any) -> datetime | None:
    """Best-effort parse for optional datetime fields."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class MemoryCategory(Enum):
    """
    Classification of memory types.

    Each category has different retention policies:
    - ARCHITECTURE, DECISION, PATTERN, GOTCHA: Long-term (no expiry)
    - PROGRESS: Short-term (7 days default)
    - CONTEXT: Medium-term (30 days default)
    - USER_PREFERENCE: Permanent (global user settings)
    """

    ARCHITECTURE = "architecture"  # How the system is structured
    DECISION = "decision"  # Why X was chosen over Y
    PATTERN = "pattern"  # Conventions, how things are done
    GOTCHA = "gotcha"  # Non-obvious pitfalls to avoid
    PROGRESS = "progress"  # Current work status (auto-expires)
    CONTEXT = "context"  # Deadlines, preferences (auto-expires)
    USER_PREFERENCE = "preference"  # User's coding style, tool preferences

    @classmethod
    def from_string(cls, value: str) -> MemoryCategory:
        """Convert string to MemoryCategory, case-insensitive."""
        value_lower = value.lower().strip()
        for category in cls:
            if category.value == value_lower:
                return category
        # Default to CONTEXT for unknown categories
        return cls.CONTEXT

    @property
    def default_expiry_days(self) -> int | None:
        """Get default expiry in days for this category, or None for no expiry."""
        return DEFAULT_EXPIRY_DAYS.get(self)

    @property
    def decay_rate(self) -> float:
        """
        Get confidence decay rate per day for this category.

        Higher decay = faster confidence loss.
        Returns 0.0 for categories that don't decay.
        """
        return DECAY_RATES.get(self, 0.0)

    @property
    def priority(self) -> int:
        """
        Get display priority for this category.

        Lower number = higher priority (shown first).
        """
        return CATEGORY_PRIORITIES.get(self, 10)

    @property
    def emoji(self) -> str:
        """Get emoji icon for this category."""
        return CATEGORY_EMOJIS.get(self, "📝")


DEFAULT_EXPIRY_DAYS.update(
    {
        MemoryCategory.ARCHITECTURE: None,
        MemoryCategory.DECISION: None,
        MemoryCategory.PATTERN: None,
        MemoryCategory.GOTCHA: None,
        MemoryCategory.PROGRESS: 7,
        MemoryCategory.CONTEXT: 30,
        MemoryCategory.USER_PREFERENCE: None,
    }
)
DECAY_RATES.update(
    {
        MemoryCategory.ARCHITECTURE: 0.0,
        MemoryCategory.DECISION: 0.0,
        MemoryCategory.PATTERN: 0.0,
        MemoryCategory.GOTCHA: 0.0,
        MemoryCategory.PROGRESS: 0.1,
        MemoryCategory.CONTEXT: 0.03,
        MemoryCategory.USER_PREFERENCE: 0.0,
    }
)
CATEGORY_PRIORITIES.update(
    {
        MemoryCategory.ARCHITECTURE: 1,
        MemoryCategory.DECISION: 2,
        MemoryCategory.PATTERN: 3,
        MemoryCategory.GOTCHA: 4,
        MemoryCategory.USER_PREFERENCE: 5,
        MemoryCategory.CONTEXT: 6,
        MemoryCategory.PROGRESS: 7,
    }
)
CATEGORY_EMOJIS.update(
    {
        MemoryCategory.ARCHITECTURE: "🏗️",
        MemoryCategory.DECISION: "⚖️",
        MemoryCategory.PATTERN: "📐",
        MemoryCategory.GOTCHA: "⚠️",
        MemoryCategory.PROGRESS: "🔄",
        MemoryCategory.CONTEXT: "📋",
        MemoryCategory.USER_PREFERENCE: "👤",
    }
)
CATEGORY_HEADERS.update(
    {
        MemoryCategory.ARCHITECTURE: "## Architecture",
        MemoryCategory.DECISION: "## Key Decisions",
        MemoryCategory.PATTERN: "## Patterns & Conventions",
        MemoryCategory.GOTCHA: "## Gotchas & Pitfalls",
        MemoryCategory.PROGRESS: "## Current Progress",
        MemoryCategory.CONTEXT: "## Context",
        MemoryCategory.USER_PREFERENCE: "## User Preferences",
    }
)


@dataclass
class Memory:
    """
    A single memory item with metadata.

    Attributes:
        id: Unique identifier (auto-generated if not provided)
        category: Type of memory (architecture, decision, etc.)
        content: The actual memory content
        tags: Searchable tags for retrieval
        confidence: 0.0-1.0, decays over time for some categories
        created_at: When this memory was created
        updated_at: When this memory was last updated
        expires_at: When this memory should be auto-deleted (optional)
        source: Where this memory came from (file, conversation, etc.)
        project: Project identifier (for project-specific memories)
        metadata: Additional arbitrary metadata
    """

    content: str
    category: MemoryCategory
    id: str = field(default_factory=lambda: f"mem-{uuid.uuid4().hex[:8]}")
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    source: str = "conversation"
    project: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set default expiry based on category if not provided."""
        self.tags = _normalize_tags(self.tags)
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError):
            confidence = 1.0
        self.confidence = max(0.0, min(1.0, confidence))

        if self.expires_at is None:
            expiry_days = self.category.default_expiry_days
            if expiry_days is not None:
                self.expires_at = self.created_at + timedelta(days=expiry_days)

    @property
    def is_expired(self) -> bool:
        """Check if this memory has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    @property
    def content_hash(self) -> str:
        """Get hash of content for deduplication."""
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    @property
    def days_old(self) -> int:
        """Get age of memory in days."""
        return (datetime.now() - self.created_at).days

    def decay_confidence(self) -> None:
        """Apply confidence decay based on category and age."""
        decay_rate = self.category.decay_rate
        if decay_rate > 0:
            days_since_update = (datetime.now() - self.updated_at).days
            decay = decay_rate * days_since_update
            self.confidence = max(0.0, self.confidence - decay)

    def refresh(self) -> None:
        """Refresh memory, resetting confidence and extending expiry."""
        self.confidence = 1.0
        self.updated_at = datetime.now()

        expiry_days = self.category.default_expiry_days
        if expiry_days is not None:
            self.expires_at = datetime.now() + timedelta(days=expiry_days)

    def to_dict(self) -> dict[str, Any]:
        """Serialize memory to dictionary."""
        return {
            "id": self.id,
            "category": self.category.value,
            "content": self.content,
            "tags": list(self.tags),
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source": self.source,
            "project": self.project,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        """Deserialize memory from dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Memory payload must be a dictionary")

        now = datetime.now()
        confidence_raw = data.get("confidence", 1.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 1.0

        metadata_raw = data.get("metadata", {})
        if not isinstance(metadata_raw, dict):
            metadata_raw = {}

        return cls(
            id=data.get("id", f"mem-{uuid.uuid4().hex[:8]}"),
            category=MemoryCategory.from_string(data.get("category", "context")),
            content=data["content"],
            tags=_normalize_tags(data.get("tags")),
            confidence=confidence,
            created_at=_parse_datetime(data.get("created_at"), fallback=now),
            updated_at=_parse_datetime(data.get("updated_at"), fallback=now),
            expires_at=_parse_optional_datetime(data.get("expires_at")),
            source=str(data.get("source", "unknown")),
            project=str(data["project"]) if data.get("project") is not None else None,
            metadata=metadata_raw,
        )

    def to_markdown(self, include_metadata: bool = False) -> str:
        """Format memory as markdown for display or AGENTS.md export."""
        lines = [f"- {self.content}"]

        if include_metadata:
            lines.append(f"  - ID: `{self.id}`")
            lines.append(f"  - Category: {self.category.emoji} {self.category.value}")
            if self.tags:
                lines.append(f"  - Tags: {', '.join(self.tags)}")
            lines.append(f"  - Confidence: {self.confidence:.0%}")
            lines.append(f"  - Created: {self.created_at.strftime('%Y-%m-%d')}")

        return "\n".join(lines)

    def matches_query(self, query: str) -> bool:
        """Check if this memory matches a search query."""
        query_lower = query.lower()

        # Check content
        content_lower = self.content.lower()
        if query_lower in content_lower:
            return True

        # Check tags
        for tag in self.tags:
            tag_lower = tag.lower()
            if query_lower in tag_lower:
                return True

        # Check category
        if query_lower in self.category.value:
            return True

        return False

    def similarity_score(self, other: Memory) -> float:
        """
        Calculate Jaccard similarity with another memory.

        Used for deduplication - higher score means more similar.
        Returns 0.0-1.0.
        """
        # Tokenize content
        tokens_self = set(self.content.lower().split())
        tokens_other = set(other.content.lower().split())

        if not tokens_self or not tokens_other:
            return 0.0

        intersection = tokens_self & tokens_other
        union = tokens_self | tokens_other

        return len(intersection) / len(union) if union else 0.0


@dataclass
class MemoryStore:
    """
    Collection of memories with search and management capabilities.

    Attributes:
        memories: List of all memories
        project: Project identifier for this store
        created_at: When this store was created
        updated_at: When this store was last modified
    """

    memories: list[Memory] = field(default_factory=list)
    project: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add(self, memory: Memory, deduplicate: bool = True) -> Memory:
        """Add memory and return the stored instance."""
        stored_memory, _ = self.add_with_status(memory, deduplicate=deduplicate)
        return stored_memory

    def add_with_status(self, memory: Memory, deduplicate: bool = True) -> tuple[Memory, bool]:
        """
        Add a memory to the store.

        If deduplicate=True, checks for similar existing memories and
        updates them instead of creating duplicates.
        """
        if deduplicate:
            # Check for similar memories (Jaccard similarity > 0.8)
            for existing in self.memories:
                if existing.category == memory.category:
                    similarity = existing.similarity_score(memory)
                    if similarity > SIMILARITY_DEDUP_THRESHOLD:
                        # Update existing memory instead of creating duplicate
                        existing.content = memory.content
                        existing.tags = _normalize_tags(existing.tags + memory.tags)
                        existing.refresh()
                        self.updated_at = datetime.now()
                        return existing, False

        # Check for exact hash match
        new_hash = memory.content_hash
        for existing in self.memories:
            if existing.content_hash == new_hash:
                existing.refresh()
                self.updated_at = datetime.now()
                return existing, False

        # Add new memory
        self.memories.append(memory)
        self.updated_at = datetime.now()
        return memory, True

    def get(self, memory_id: str) -> Memory | None:
        """Get a memory by ID."""
        for memory in self.memories:
            if memory.id == memory_id:
                return memory
        return None

    def remove(self, memory_id: str) -> bool:
        """Remove a memory by ID. Returns True if removed."""
        for i, memory in enumerate(self.memories):
            if memory.id == memory_id:
                del self.memories[i]
                self.updated_at = datetime.now()
                return True
        return False

    def search(
        self,
        query: str | None = None,
        category: MemoryCategory | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int | None = None,
    ) -> list[Memory]:
        """
        Search memories with filters.

        Args:
            query: Text to search for in content and tags
            category: Filter by category
            tags: Filter by any of these tags
            min_confidence: Minimum confidence threshold
            limit: Maximum number of results

        Returns:
            List of matching memories, sorted by relevance
        """
        results = []

        for memory in self.memories:
            # Skip expired memories
            if memory.is_expired:
                continue

            # Apply confidence filter
            if memory.confidence < min_confidence:
                continue

            # Apply category filter
            if category is not None and memory.category != category:
                continue

            # Apply tags filter
            if tags is not None:
                if not any(tag in memory.tags for tag in tags):
                    continue

            # Apply query filter
            if query is not None and not memory.matches_query(query):
                continue

            results.append(memory)

        # Sort by category priority, then confidence, then recency
        results.sort(
            key=lambda m: (
                m.category.priority,
                -m.confidence,
                -m.updated_at.timestamp(),
            )
        )

        if limit is not None:
            results = results[:limit]

        return results

    def by_category(self) -> dict[MemoryCategory, list[Memory]]:
        """Group memories by category."""
        grouped: dict[MemoryCategory, list[Memory]] = {}

        for memory in self.memories:
            if memory.is_expired:
                continue

            grouped.setdefault(memory.category, []).append(memory)

        # Sort each group by confidence
        for category in grouped:
            grouped[category].sort(key=lambda m: -m.confidence)

        return grouped

    def decay_all(self) -> None:
        """Apply confidence decay to all memories."""
        changed = False
        for memory in self.memories:
            previous_confidence = memory.confidence
            memory.decay_confidence()
            if memory.confidence != previous_confidence:
                changed = True
        if changed:
            self.updated_at = datetime.now()

    def prune_expired(self) -> int:
        """Remove expired memories. Returns count of removed."""
        return self._prune(lambda memory: not memory.is_expired)

    def prune_low_confidence(self, threshold: float = 0.1) -> int:
        """Remove memories below confidence threshold. Returns count of removed."""
        return self._prune(lambda memory: memory.confidence >= threshold)

    def _prune(self, keep_predicate: Callable[[Memory], bool]) -> int:
        """Prune memories using a keep predicate and return removed count."""
        before_count = len(self.memories)
        self.memories = [memory for memory in self.memories if keep_predicate(memory)]
        removed = before_count - len(self.memories)
        if removed > 0:
            self.updated_at = datetime.now()
        return removed

    @property
    def count(self) -> int:
        """Get total number of non-expired memories."""
        return sum(1 for memory in self.memories if not memory.is_expired)

    @property
    def active_count(self) -> int:
        """Get number of high-confidence memories (> 0.5)."""
        return sum(1 for memory in self.memories if not memory.is_expired and memory.confidence > 0.5)

    def to_dict(self) -> dict[str, Any]:
        """Serialize store to dictionary."""
        return {
            "project": self.project,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "memories": [m.to_dict() for m in self.memories],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryStore:
        """Deserialize store from dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Memory store payload must be a dictionary")

        now = datetime.now()
        store = cls(
            project=data.get("project"),
            created_at=_parse_datetime(data.get("created_at"), fallback=now),
            updated_at=_parse_datetime(data.get("updated_at"), fallback=now),
        )

        memories_data = data.get("memories", [])
        if not isinstance(memories_data, list):
            raise ValueError("Memory store 'memories' must be a list")

        for memory_data in memories_data:
            memory = Memory.from_dict(memory_data)
            store.memories.append(memory)

        return store

    def to_markdown(self, max_lines: int = 150) -> str:
        """
        Generate markdown summary for AGENTS.md.

        Prioritizes important memories and stays under line budget.
        """
        lines = []
        lines.append("<!-- MEMORY:START -->")
        lines.append(
            f"_Last updated: {datetime.now().strftime('%Y-%m-%d')} | "
            f"{self.active_count} active memories, {self.count} total_"
        )
        lines.append("")

        grouped = self.by_category()

        # Sort categories by priority
        sorted_categories = sorted(grouped.keys(), key=lambda c: c.priority)

        for category in sorted_categories:
            memories = grouped[category]
            if not memories:
                continue

            header = CATEGORY_HEADERS.get(category, f"## {category.value.title()}")
            lines.append(header)

            # Add top memories for this category
            for memory in memories[:MAX_MEMORIES_PER_CATEGORY_IN_MARKDOWN]:
                lines.append(memory.to_markdown(include_metadata=False))

            lines.append("")

            # Check line budget
            if len(lines) >= max_lines - 5:
                lines.append("_... additional memories available via memory_search ..._")
                break

        lines.append("<!-- MEMORY:END -->")
        return "\n".join(lines)

    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about the memory store."""
        grouped = self.by_category()

        stats = {
            "total": len(self.memories),
            "active": self.active_count,
            "expired": len([m for m in self.memories if m.is_expired]),
            "by_category": {cat.value: len(mems) for cat, mems in grouped.items()},
            "avg_confidence": (sum(m.confidence for m in self.memories) / len(self.memories) if self.memories else 0.0),
            "oldest": min((m.created_at for m in self.memories), default=None),
            "newest": max((m.created_at for m in self.memories), default=None),
        }

        return stats

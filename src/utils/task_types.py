"""
Task Types - Core data structures for the Task orchestration system.

This module provides the foundational types for task management:
- Task: Individual work item with dependencies, status, and metadata
- TaskList: Collection of tasks with dependency resolution and coordination

The task system enables:
- Cross-session persistence (tasks survive restarts)
- Dependency management (blockedBy/blocks relationships)
- Multi-agent coordination (shared task lists)
- Parallel execution (available tasks are unblocked and unowned)

Reference: Task system architecture
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterator, Literal


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


TaskStatusType = Literal["pending", "in_progress", "completed"]


@dataclass
class Task:
    """
    A single task in the task orchestration system.

    Attributes:
        id: Unique task identifier (e.g., "task-a1b2c3d4")
        subject: Brief imperative title ("Implement JWT authentication")
        description: Detailed requirements and acceptance criteria
        status: Current state (pending → in_progress → completed)
        active_form: Present-tense form for spinner display ("Implementing JWT...")
        owner: Agent ID that claimed this task (None if unclaimed)
        blocked_by: List of task IDs that must complete before this task
        blocks: List of task IDs that depend on this task
        metadata: Arbitrary key-value pairs for tracking (feature, phase, priority)
        created_at: Timestamp when task was created
        updated_at: Timestamp of last modification
    """

    id: str
    subject: str
    description: str
    status: TaskStatusType = "pending"
    active_form: str | None = None
    owner: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(
        cls,
        subject: str,
        description: str,
        *,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ) -> Task:
        """
        Factory method to create a new task with generated ID.

        Args:
            subject: Imperative title (e.g., "Implement X", "Add Y", "Fix Z")
            description: Detailed requirements
            active_form: Spinner text (e.g., "Implementing X...")
            metadata: Tracking data (feature, phase, priority)
            blocked_by: Task IDs this depends on
            blocks: Task IDs that depend on this

        Returns:
            New Task instance with unique ID
        """
        now = datetime.now()
        return cls(
            id=f"task-{uuid.uuid4().hex[:8]}",
            subject=subject,
            description=description,
            status="pending",
            active_form=active_form or f"Working on: {subject}",
            owner=None,
            blocked_by=list(blocked_by) if blocked_by else [],
            blocks=list(blocks) if blocks else [],
            metadata=dict(metadata) if metadata else {},
            created_at=now,
            updated_at=now,
        )

    def is_available(self) -> bool:
        """
        Check if task is ready to be worked on.

        A task is available when:
        - Status is pending (not started or completed)
        - No owner has claimed it
        - All blocking dependencies are resolved

        Returns:
            True if task can be started
        """
        return self.status == "pending" and self.owner is None and len(self.blocked_by) == 0

    def is_blocked(self) -> bool:
        """Check if task is waiting on dependencies."""
        return self.status == "pending" and len(self.blocked_by) > 0

    def mark_in_progress(self, owner: str | None = None) -> None:
        """
        Mark task as being actively worked on.

        Args:
            owner: Optional agent ID claiming the task
        """
        self.status = "in_progress"
        self.owner = owner
        self.updated_at = datetime.now()

    def mark_completed(self) -> None:
        """Mark task as finished."""
        self.status = "completed"
        self.updated_at = datetime.now()

    def add_blocker(self, task_id: str) -> None:
        """Add a dependency that blocks this task."""
        if task_id not in self.blocked_by:
            self.blocked_by.append(task_id)
            self.updated_at = datetime.now()

    def remove_blocker(self, task_id: str) -> bool:
        """
        Remove a dependency blocker.

        Returns:
            True if blocker was removed
        """
        if task_id in self.blocked_by:
            self.blocked_by.remove(task_id)
            self.updated_at = datetime.now()
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize task to dictionary for storage."""
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "active_form": self.active_form,
            "owner": self.owner,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """Deserialize task from dictionary."""
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data["description"],
            status=data.get("status", "pending"),
            active_form=data.get("active_form"),
            owner=data.get("owner"),
            blocked_by=list(data.get("blocked_by", [])),
            blocks=list(data.get("blocks", [])),
            metadata=dict(data.get("metadata", {})),
            created_at=(datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now()),
            updated_at=(datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now()),
        )

    def __repr__(self) -> str:
        status_icon = {"pending": "◼", "in_progress": "▶", "completed": "✔"}.get(self.status, "?")
        return f"Task({status_icon} {self.id}: {self.subject})"


@dataclass
class TaskList:
    """
    Collection of tasks with dependency management and coordination.

    TaskLists support:
    - Cross-session persistence via TaskStorage
    - Multi-agent coordination (shared via TASK_LIST_ID)
    - Automatic dependency resolution on task completion
    - Available/blocked/in-progress task filtering

    Attributes:
        id: Unique list identifier for cross-session sharing
        tasks: Dictionary of task_id -> Task
        created_at: When the list was created
        updated_at: Last modification timestamp
    """

    id: str
    tasks: dict[str, Task] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(cls, list_id: str | None = None) -> TaskList:
        """
        Create a new task list with optional ID.

        Args:
            list_id: Optional ID for cross-session sharing

        Returns:
            New TaskList instance
        """
        now = datetime.now()
        return cls(
            id=list_id or f"tasklist-{uuid.uuid4().hex[:8]}",
            tasks={},
            created_at=now,
            updated_at=now,
        )

    def add_task(self, task: Task) -> None:
        """
        Add a task to the list.

        Also updates bidirectional dependency links.
        """
        self.tasks[task.id] = task

        # Ensure bidirectional links are consistent
        for blocker_id in task.blocked_by:
            blocker = self.tasks.get(blocker_id)
            if blocker and task.id not in blocker.blocks:
                blocker.blocks.append(task.id)

        for blocked_id in task.blocks:
            blocked = self.tasks.get(blocked_id)
            if blocked and task.id not in blocked.blocked_by:
                blocked.blocked_by.append(task.id)

        self._update_timestamp()

    def get_task(self, task_id: str) -> Task | None:
        """Get task by ID."""
        return self.tasks.get(task_id)

    def remove_task(self, task_id: str) -> Task | None:
        """
        Remove a task and clean up its dependency links.

        Returns:
            The removed task, or None if not found
        """
        task = self.tasks.pop(task_id, None)
        if task is None:
            return None

        # Remove from other tasks' dependency lists
        for other in self.tasks.values():
            if task_id in other.blocked_by:
                other.blocked_by.remove(task_id)
            if task_id in other.blocks:
                other.blocks.remove(task_id)

        self._update_timestamp()
        return task

    def complete_task(self, task_id: str) -> list[str]:
        """
        Mark a task as completed and unblock dependents.

        Args:
            task_id: ID of the task to complete

        Returns:
            List of task IDs that became available (newly unblocked)
        """
        task = self.tasks.get(task_id)
        if not task:
            return []

        task.mark_completed()

        # Unblock dependent tasks and track newly available ones
        newly_available: list[str] = []
        for other_id, other in self.tasks.items():
            if task_id in other.blocked_by:
                other.blocked_by.remove(task_id)
                # Check if this task is now available
                if other.is_available():
                    newly_available.append(other_id)

        self._update_timestamp()
        return newly_available

    def get_available_tasks(self) -> list[Task]:
        """Get all tasks that are ready to work on."""
        return [t for t in self.tasks.values() if t.is_available()]

    def get_blocked_tasks(self) -> list[Task]:
        """Get all tasks waiting on dependencies."""
        return [t for t in self.tasks.values() if t.is_blocked()]

    def get_in_progress_tasks(self) -> list[Task]:
        """Get all tasks currently being worked on."""
        return [t for t in self.tasks.values() if t.status == "in_progress"]

    def get_completed_tasks(self) -> list[Task]:
        """Get all finished tasks."""
        return [t for t in self.tasks.values() if t.status == "completed"]

    def get_pending_tasks(self) -> list[Task]:
        """Get all unstarted tasks (both available and blocked)."""
        return [t for t in self.tasks.values() if t.status == "pending"]

    def get_stats(self) -> dict[str, int]:
        """Get task count statistics."""
        tasks = list(self.tasks.values())
        return {
            "total": len(tasks),
            "pending": len([t for t in tasks if t.status == "pending"]),
            "in_progress": len([t for t in tasks if t.status == "in_progress"]),
            "completed": len([t for t in tasks if t.status == "completed"]),
            "available": len([t for t in tasks if t.is_available()]),
            "blocked": len([t for t in tasks if t.is_blocked()]),
        }

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self) -> Iterator[Task]:
        return iter(self.tasks.values())

    def __contains__(self, task_id: str) -> bool:
        return task_id in self.tasks

    def to_dict(self) -> dict[str, Any]:
        """Serialize task list for storage."""
        return {
            "id": self.id,
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskList:
        """Deserialize task list from storage."""
        task_list = cls(
            id=data["id"],
            tasks={},
            created_at=(datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now()),
            updated_at=(datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now()),
        )

        # Deserialize tasks
        for tid, tdata in data.get("tasks", {}).items():
            task_list.tasks[tid] = Task.from_dict(tdata)

        return task_list

    def _update_timestamp(self) -> None:
        """Update the last modified timestamp."""
        self.updated_at = datetime.now()

    def __repr__(self) -> str:
        stats = self.get_stats()
        return f"TaskList({self.id}: {stats['completed']}/{stats['total']} completed)"

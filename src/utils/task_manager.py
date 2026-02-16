"""
Task Manager - Central coordinator for task operations.

The TaskManager provides a high-level interface for:
- Creating, updating, and deleting tasks
- Managing dependencies (blocked_by/blocks)
- Persisting task state to disk
- Multi-session coordination

This is the bridge between task tools and the underlying TaskList/TaskStorage.
Session holds a TaskManager instance and injects it into tool invocations.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Callable, Iterator, TypeVar

from utils.task_storage import TaskStorage, get_task_storage
from utils.task_types import Task, TaskList

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TaskManagerError(Exception):
    """Base exception for task manager operations."""

    pass


class TaskNotFoundError(TaskManagerError):
    """Task with given ID does not exist."""

    pass


class TaskDependencyError(TaskManagerError):
    """Invalid dependency operation (e.g., circular dependency)."""

    pass


class TaskManager:
    """
    Central coordinator for task operations.

    Features:
    - CRUD operations on tasks
    - Automatic dependency management
    - Persistent storage
    - Cross-session coordination via shared task list IDs

    Usage:
        # Create with auto-generated list ID
        manager = TaskManager()

        # Create with shared list ID (for multi-session)
        manager = TaskManager(list_id="shared-project-tasks")

        # Load existing list
        manager = TaskManager(list_id="existing-list-id")

        # Create tasks
        task = manager.create_task(
            subject="Implement feature",
            description="Detailed requirements..."
        )

        # Update status
        manager.start_task(task.id)  # pending → in_progress
        manager.complete_task(task.id)  # in_progress → completed
    """

    ENV_TASK_LIST_ID = "PICHU_TASK_LIST_ID"

    def __init__(
        self,
        list_id: str | None = None,
        storage: TaskStorage | None = None,
        auto_save: bool = True,
    ):
        """
        Initialize task manager.

        Args:
            list_id: Task list ID for cross-session sharing.
                     Falls back to environment variable PICHU_TASK_LIST_ID.
                     If none provided, creates a new unique ID.
            storage: TaskStorage instance (defaults to global storage).
            auto_save: Whether to automatically save after each operation.
        """
        self._storage = storage or get_task_storage()
        self._auto_save = auto_save

        # Determine list ID
        self._list_id = list_id or os.environ.get(self.ENV_TASK_LIST_ID) or None

        # Load or create task list
        self._task_list = self._load_or_create_list()

        logger.info(f"TaskManager initialized with list: {self._task_list.id}")

    def _load_or_create_list(self) -> TaskList:
        """Load existing task list or create new one."""
        if self._list_id:
            with self._storage.acquire_lock(self._list_id):
                existing = self._storage.load(self._list_id)
                if existing:
                    logger.debug(f"Loaded existing task list: {self._list_id}")
                    return existing

                created = TaskList.create(self._list_id)
                self._storage.save(created)
                logger.debug(f"Created new task list: {created.id}")
                return created

        task_list = TaskList.create()
        self._list_id = task_list.id
        self._storage.save(task_list)
        logger.debug(f"Created new task list: {task_list.id}")
        return task_list

    def _refresh_from_storage(self) -> None:
        """Reload the latest task list snapshot, if present."""
        latest = self._storage.load(self._task_list.id)
        if latest is not None:
            self._task_list = latest

    def _run_locked_mutation(self, mutation: Callable[[TaskList], T]) -> T:
        """
        Execute a mutation against the latest persisted task list snapshot.

        This keeps multi-session updates consistent by locking at list level
        across read-modify-write operations.
        """
        with self._storage.acquire_lock(self._task_list.id):
            latest = self._storage.load(self._task_list.id)
            if latest is not None:
                self._task_list = latest

            result = mutation(self._task_list)
            self._task_list.updated_at = datetime.now()

            if self._auto_save:
                self._storage.save(self._task_list)

            return result

    @staticmethod
    def _get_task_or_raise(task_list: TaskList, task_id: str) -> Task:
        """Fetch a task from a list, raising if it does not exist."""
        task = task_list.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task

    @staticmethod
    def _validate_start(task: Task, owner: str | None = None) -> None:
        """Validate that a task can transition to in_progress."""
        if task.status == "completed":
            raise TaskManagerError(f"Cannot start completed task: {task.id}")
        if task.status == "in_progress":
            raise TaskManagerError(f"Task already in progress: {task.id}")
        if task.is_blocked():
            blockers = ", ".join(task.blocked_by)
            raise TaskManagerError(f"Task is blocked by: {blockers}")
        if task.owner and owner and task.owner != owner:
            raise TaskManagerError(f"Task {task.id} is already owned by {task.owner}; cannot claim as {owner}")

    def _has_path(self, start_task_id: str, target_task_id: str, task_list: TaskList) -> bool:
        """Return True when target is reachable from start via blocks edges."""
        to_visit = [start_task_id]
        visited: set[str] = set()

        while to_visit:
            current_id = to_visit.pop()
            if current_id == target_task_id:
                return True
            if current_id in visited:
                continue
            visited.add(current_id)

            current = task_list.get_task(current_id)
            if current is None:
                continue
            to_visit.extend(current.blocks)

        return False

    @property
    def list_id(self) -> str:
        """Get the current task list ID."""
        return self._task_list.id

    @property
    def task_count(self) -> int:
        """Get total number of tasks."""
        self._refresh_from_storage()
        return len(self._task_list)

    def save(self) -> None:
        """Persist current task list to storage."""
        with self._storage.acquire_lock(self._task_list.id):
            self._storage.save(self._task_list)
        logger.debug(f"Saved task list: {self._task_list.id}")

    # Task CRUD Operations

    def create_task(
        self,
        subject: str,
        description: str,
        *,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ) -> Task:
        """
        Create a new task.

        Args:
            subject: Brief imperative title
            description: Detailed requirements
            active_form: Spinner text (auto-generated if not provided)
            metadata: Tracking data (feature, phase, priority)
            blocked_by: Task IDs this depends on
            blocks: Task IDs that depend on this

        Returns:
            The created Task

        Raises:
            TaskNotFoundError: If a dependency task ID doesn't exist
        """

        def mutate(task_list: TaskList) -> Task:
            if blocked_by:
                for dep_id in blocked_by:
                    if dep_id not in task_list:
                        raise TaskNotFoundError(f"Blocker task not found: {dep_id}")

            if blocks:
                for dep_id in blocks:
                    if dep_id not in task_list:
                        raise TaskNotFoundError(f"Blocked task not found: {dep_id}")

            task = Task.create(
                subject=subject,
                description=description,
                active_form=active_form,
                metadata=metadata,
                blocked_by=blocked_by,
                blocks=blocks,
            )

            task_list.add_task(task)
            return task

        task = self._run_locked_mutation(mutate)
        logger.info(f"Created task: {task.id} - {task.subject}")
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID, or None if not found."""
        self._refresh_from_storage()
        return self._task_list.get_task(task_id)

    def get_all_tasks(self) -> Iterator[Task]:
        """Iterate over all tasks."""
        self._refresh_from_storage()
        return iter(self._task_list)

    def delete_task(self, task_id: str) -> Task:
        """
        Delete a task and clean up dependencies.

        Returns:
            The deleted task

        Raises:
            TaskNotFoundError: If task doesn't exist
        """

        def mutate(task_list: TaskList) -> Task:
            task = task_list.remove_task(task_id)
            if task is None:
                raise TaskNotFoundError(f"Task not found: {task_id}")
            return task

        task = self._run_locked_mutation(mutate)
        logger.info(f"Deleted task: {task_id}")
        return task

    # Status Transitions

    def start_task(self, task_id: str, owner: str | None = None) -> Task:
        """
        Mark a task as in_progress.

        Args:
            task_id: ID of task to start
            owner: Optional agent ID claiming the task

        Returns:
            The updated task

        Raises:
            TaskNotFoundError: If task doesn't exist
            TaskManagerError: If task cannot be started (blocked/completed)
        """

        def mutate(task_list: TaskList) -> Task:
            task = self._get_task_or_raise(task_list, task_id)
            self._validate_start(task, owner=owner)
            task.mark_in_progress(owner=owner or task.owner)
            return task

        task = self._run_locked_mutation(mutate)
        logger.info(f"Started task: {task_id}")
        return task

    def requeue_task(self, task_id: str, owner: str | None = None) -> Task:
        """
        Move an in-progress task back to pending.

        Args:
            task_id: ID of task to requeue
            owner: Optional owner to keep when re-queued (defaults to unowned)

        Returns:
            Updated task in pending state

        Raises:
            TaskNotFoundError: If task doesn't exist
            TaskManagerError: If task is not in progress
        """

        def mutate(task_list: TaskList) -> Task:
            task = self._get_task_or_raise(task_list, task_id)
            if task.status != "in_progress":
                raise TaskManagerError(f"Task {task_id} can only be set to pending from in_progress")
            task.status = "pending"
            task.owner = owner
            task.updated_at = datetime.now()
            return task

        task = self._run_locked_mutation(mutate)
        logger.info(f"Re-queued task: {task_id}")
        return task

    def complete_task(self, task_id: str) -> list[str]:
        """
        Mark a task as completed and unblock dependents.

        Args:
            task_id: ID of task to complete

        Returns:
            List of task IDs that became available (newly unblocked)

        Raises:
            TaskNotFoundError: If task doesn't exist
            TaskManagerError: If task is not in progress
        """

        def mutate(task_list: TaskList) -> list[str]:
            task = self._get_task_or_raise(task_list, task_id)
            if task.status != "in_progress":
                raise TaskManagerError(f"Task {task_id} must be in_progress before completion")
            return task_list.complete_task(task_id)

        newly_available = self._run_locked_mutation(mutate)
        logger.info(f"Completed task: {task_id}, unblocked: {newly_available}")
        return newly_available

    def assign_owner(self, task_id: str, owner: str | None) -> Task:
        """
        Assign or clear task owner without changing status.

        Args:
            task_id: ID of task to update
            owner: Owner identifier or None to clear owner
        """

        def mutate(task_list: TaskList) -> Task:
            task = self._get_task_or_raise(task_list, task_id)
            task.owner = owner
            task.updated_at = datetime.now()
            return task

        task = self._run_locked_mutation(mutate)
        logger.debug(f"Updated owner for task {task_id}: {owner}")
        return task

    # Dependency Management

    def add_dependency(self, task_id: str, blocker_id: str) -> None:
        """
        Add a dependency: task_id is blocked by blocker_id.

        Args:
            task_id: The task that will be blocked
            blocker_id: The task that blocks it

        Raises:
            TaskNotFoundError: If either task doesn't exist
            TaskDependencyError: If this would create a cycle
        """

        def mutate(task_list: TaskList) -> None:
            if task_id == blocker_id:
                raise TaskDependencyError("A task cannot depend on itself")

            task = self._get_task_or_raise(task_list, task_id)
            blocker = self._get_task_or_raise(task_list, blocker_id)

            if blocker_id in task.blocked_by:
                return

            if self._has_path(task_id, blocker_id, task_list):
                raise TaskDependencyError(
                    f"Circular dependency detected: adding {blocker_id} -> {task_id} would create a cycle"
                )

            task.blocked_by.append(blocker_id)
            blocker.blocks.append(task_id)
            task.updated_at = datetime.now()
            blocker.updated_at = datetime.now()

        self._run_locked_mutation(mutate)
        logger.debug(f"Added dependency: {task_id} blocked by {blocker_id}")

    def remove_dependency(self, task_id: str, blocker_id: str) -> bool:
        """
        Remove a dependency.

        Returns:
            True if dependency existed and was removed
        """

        def mutate(task_list: TaskList) -> bool:
            task = self._get_task_or_raise(task_list, task_id)
            blocker = self._get_task_or_raise(task_list, blocker_id)

            removed = False

            if blocker_id in task.blocked_by:
                task.blocked_by.remove(blocker_id)
                removed = True

            if task_id in blocker.blocks:
                blocker.blocks.remove(task_id)
                removed = True

            if removed:
                task.updated_at = datetime.now()
                blocker.updated_at = datetime.now()

            return removed

        removed = self._run_locked_mutation(mutate)
        if removed:
            logger.debug(f"Removed dependency: {task_id} no longer blocked by {blocker_id}")
        return removed

    # Query Methods

    def get_available_tasks(self) -> list[Task]:
        """Get tasks that are ready to work on."""
        self._refresh_from_storage()
        return self._task_list.get_available_tasks()

    def get_blocked_tasks(self) -> list[Task]:
        """Get tasks waiting on dependencies."""
        self._refresh_from_storage()
        return self._task_list.get_blocked_tasks()

    def get_in_progress_tasks(self) -> list[Task]:
        """Get tasks currently being worked on."""
        self._refresh_from_storage()
        return self._task_list.get_in_progress_tasks()

    def get_completed_tasks(self) -> list[Task]:
        """Get finished tasks."""
        self._refresh_from_storage()
        return self._task_list.get_completed_tasks()

    def get_stats(self) -> dict[str, int]:
        """Get task count statistics."""
        self._refresh_from_storage()
        return self._task_list.get_stats()

    def get_summary(self) -> str:
        """Get a brief text summary of task status."""
        stats = self.get_stats()

        if stats["total"] == 0:
            return "No tasks"

        return (
            f"{stats['completed']}/{stats['total']} completed, "
            f"{stats['available']} available, "
            f"{stats['in_progress']} in progress, "
            f"{stats['blocked']} blocked"
        )

    # Cleanup

    def clear_completed(self) -> int:
        """
        Remove all completed tasks.

        Returns:
            Number of tasks removed
        """

        def mutate(task_list: TaskList) -> int:
            completed_ids = [t.id for t in task_list.get_completed_tasks()]
            for task_id in completed_ids:
                task_list.remove_task(task_id)
            return len(completed_ids)

        removed = self._run_locked_mutation(mutate)
        if removed:
            logger.info(f"Cleared {removed} completed tasks")
        return removed

    def reset(self) -> None:
        """Clear all tasks and start fresh."""
        with self._storage.acquire_lock(self._task_list.id):
            self._task_list = TaskList.create(self._list_id)
            if self._auto_save:
                self._storage.save(self._task_list)
        logger.info(f"Reset task list: {self._list_id}")

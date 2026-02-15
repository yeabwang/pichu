"""Task manager regression tests for lifecycle and coordination rules."""

from __future__ import annotations

import pytest

from utils.task_manager import (
    TaskDependencyError,
    TaskManager,
    TaskManagerError,
)
from utils.task_storage import TaskStorage


def _manager(tmp_path, list_id: str) -> TaskManager:
    storage = TaskStorage(base_dir=tmp_path / "tasks")
    return TaskManager(list_id=list_id, storage=storage)


def test_complete_requires_in_progress(tmp_path):
    manager = _manager(tmp_path, "status-flow")
    task = manager.create_task("Implement parser", "Implement parser with tests.")

    with pytest.raises(TaskManagerError, match="in_progress"):
        manager.complete_task(task.id)

    manager.start_task(task.id, owner="agent-1")
    manager.complete_task(task.id)

    completed = manager.get_task(task.id)
    assert completed is not None
    assert completed.status == "completed"


def test_requeue_requires_in_progress_and_clears_owner(tmp_path):
    manager = _manager(tmp_path, "requeue-flow")
    task = manager.create_task("Write docs", "Write docs for the new task manager.")

    with pytest.raises(TaskManagerError, match="in_progress"):
        manager.requeue_task(task.id)

    manager.start_task(task.id, owner="agent-42")
    manager.requeue_task(task.id)

    refreshed = manager.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.owner is None


def test_add_dependency_rejects_transitive_cycles(tmp_path):
    manager = _manager(tmp_path, "deps-flow")
    task_a = manager.create_task("A", "Task A details.")
    task_b = manager.create_task("B", "Task B details.")
    task_c = manager.create_task("C", "Task C details.")

    manager.add_dependency(task_b.id, task_a.id)
    manager.add_dependency(task_c.id, task_b.id)

    with pytest.raises(TaskDependencyError, match="Circular dependency"):
        manager.add_dependency(task_a.id, task_c.id)


def test_shared_list_refreshes_latest_state(tmp_path):
    storage = TaskStorage(base_dir=tmp_path / "tasks")
    manager_one = TaskManager(list_id="shared-list", storage=storage)
    manager_two = TaskManager(list_id="shared-list", storage=storage)

    first = manager_one.create_task("First", "First task details.")
    second = manager_two.create_task("Second", "Second task details.")

    all_ids = {task.id for task in manager_one.get_all_tasks()}
    assert first.id in all_ids
    assert second.id in all_ids

    manager_one.start_task(first.id, owner="agent-one")

    with pytest.raises(TaskManagerError, match="already in progress"):
        manager_two.start_task(first.id, owner="agent-two")

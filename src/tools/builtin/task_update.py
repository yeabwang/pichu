"""
TaskUpdate Tool - Update task status and dependencies.

This tool manages task lifecycle and dependency relationships:
- Status transitions: pending → in_progress → completed
- Add/remove dependencies (blocked_by/blocks)
- Assign owner (for multi-agent coordination)
- Delete tasks

Usage:
    # Start working on a task
    task_update(task_id="task-abc123", status="in_progress")

    # Complete a task (auto-unblocks dependents)
    task_update(task_id="task-abc123", status="completed")

    # Add a dependency
    task_update(task_id="task-def456", add_blocked_by=["task-abc123"])

    # Delete a task
    task_update(task_id="task-abc123", delete=True)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class TaskUpdateParams(BaseModel):
    """Parameters for updating a task."""

    task_id: str = Field(
        ...,
        description="ID of the task to update (e.g., 'task-abc12345').",
        min_length=1,
    )

    status: Literal["pending", "in_progress", "completed"] | None = Field(
        None,
        description="New status. Flow: pending → in_progress → completed. Always mark in_progress before starting work.",
    )

    add_blocked_by: list[str] | None = Field(
        None,
        description="Task IDs to add as blockers. 'I cannot start until these tasks complete.'",
    )

    add_blocks: list[str] | None = Field(
        None,
        description="Task IDs that this task blocks. 'These tasks cannot start until I complete.'",
    )

    remove_blocked_by: list[str] | None = Field(
        None, description="Task IDs to remove as blockers (dependency resolved)."
    )

    owner: str | None = Field(
        None,
        description="Agent ID to assign as owner. Used for multi-agent coordination.",
    )

    delete: bool = Field(
        False,
        description="Set to true to delete the task entirely. Cannot be combined with other updates.",
    )

    @model_validator(mode="after")
    def validate_delete_exclusive(self) -> "TaskUpdateParams":
        """Ensure delete is not combined with other operations."""
        if self.delete:
            has_other = any(
                [
                    self.status is not None,
                    self.add_blocked_by is not None,
                    self.add_blocks is not None,
                    self.remove_blocked_by is not None,
                    self.owner is not None,
                ]
            )
            if has_other:
                raise ValueError("delete=True cannot be combined with other update operations")
        return self


class TaskUpdateTool(Tool):
    """
    Update task status and dependencies.

    Status Flow:
        pending → in_progress → completed

    Always mark a task as in_progress before starting work.
    Never skip directly from pending to completed.

    Dependency Management:
        - add_blocked_by: "I cannot start until these complete"
        - add_blocks: "These cannot start until I complete"

    When a task is completed, all tasks it blocks are automatically
    updated (the completed task is removed from their blocked_by lists).

    Multi-Agent Coordination:
        - Set owner to claim a task for a specific agent
        - Check owner before starting work on shared task lists
    """

    name = "task_update"
    description = """Update task status, dependencies, or delete a task.

Status flow: pending → in_progress → completed
- Always mark in_progress before starting work
- Completing a task auto-unblocks dependent tasks

Dependency updates:
- add_blocked_by: "I cannot start until these complete"
- add_blocks: "These cannot start until I complete"

Special operations:
- owner: Assign task to an agent (multi-agent coordination)
- delete: Remove task entirely (cannot combine with other ops)"""

    kind = ToolKind.MEMORY
    schema = TaskUpdateParams

    def __init__(self, config: Any = None, task_manager: Any = None) -> None:
        super().__init__(config)
        self._task_manager = task_manager

    def set_task_manager(self, task_manager: Any) -> None:
        """Set the task manager (called by Session during initialization)."""
        self._task_manager = task_manager

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Update a task's status or dependencies."""
        params = TaskUpdateParams(**invocation.params)

        # Get task manager from instance (injected by Session)
        task_manager = self._task_manager

        if task_manager is None:
            return ToolResult.error_result(
                error_message="No task manager available. Tasks not initialized.",
                metadata={"task_id": params.task_id},
            )

        # Get the task
        task = task_manager.get_task(params.task_id)
        if task is None:
            return ToolResult.error_result(
                error_message=f"Task not found: {params.task_id}",
                metadata={"task_id": params.task_id},
            )

        # Handle delete
        if params.delete:
            try:
                task_manager.delete_task(params.task_id)
                return ToolResult.success_result(
                    output=f"✔ Task #{params.task_id} deleted: {task.subject}",
                    metadata={
                        "task_id": params.task_id,
                        "action": "deleted",
                    },
                )
            except Exception as e:
                return ToolResult.error_result(
                    error_message=f"Failed to delete task: {e}",
                    metadata={"task_id": params.task_id},
                )

        # Track what was updated
        updates: list[str] = []
        newly_available: list[str] = []

        try:
            # Update status
            if params.status is not None:
                old_status = task.status
                if params.status == "completed":
                    newly_available = task_manager.complete_task(params.task_id)
                elif params.status == "in_progress":
                    task_manager.start_task(params.task_id, owner=params.owner)
                elif params.status == "pending":
                    task_manager.requeue_task(params.task_id, owner=params.owner)
                else:
                    return ToolResult.error_result(
                        error_message=f"Unsupported status transition: {params.status}",
                        metadata={"task_id": params.task_id},
                    )

                updates.append(f"status: {old_status} → {params.status}")

            # Update owner (if not already set by status change)
            if params.owner is not None and params.status is None:
                task_manager.assign_owner(params.task_id, params.owner)
                updates.append(f"owner: {params.owner}")

            # Add blocked_by
            if params.add_blocked_by:
                for blocker_id in params.add_blocked_by:
                    task_manager.add_dependency(params.task_id, blocker_id)
                updates.append(f"blocked_by: +{params.add_blocked_by}")

            # Add blocks
            if params.add_blocks:
                for blocked_id in params.add_blocks:
                    task_manager.add_dependency(blocked_id, params.task_id)
                updates.append(f"blocks: +{params.add_blocks}")

            # Remove blocked_by
            if params.remove_blocked_by:
                for blocker_id in params.remove_blocked_by:
                    task_manager.remove_dependency(params.task_id, blocker_id)
                updates.append(f"blocked_by: -{params.remove_blocked_by}")

        except Exception as e:
            return ToolResult.error_result(
                error_message=f"Failed to update task: {e}",
                metadata={
                    "task_id": params.task_id,
                    "partial_updates": updates,
                },
            )

        # Build output
        task = task_manager.get_task(params.task_id)
        if task is None:
            return ToolResult.error_result(
                error_message=f"Task not found after update: {params.task_id}",
                metadata={"task_id": params.task_id},
            )

        output_lines = [f"✔ Task #{params.task_id} updated: {task.subject}"]
        for update in updates:
            output_lines.append(f"   {update}")

        if newly_available:
            output_lines.append(f"   → Unblocked: {', '.join(newly_available)}")

        return ToolResult.success_result(
            output="\n".join(output_lines),
            metadata={
                "task_id": params.task_id,
                "updates": updates,
                "newly_available": newly_available,
                "current_status": task.status,
            },
        )

"""
TaskGet Tool - Retrieve detailed information about a specific task.

This tool returns full task details including:
- Subject, description, status
- Dependency relationships (blocked_by, blocks)
- Metadata and timestamps
- Availability status

Usage:
    task_get(task_id="task-abc123")

Returns complete task information for planning and coordination.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class TaskGetParams(BaseModel):
    """Parameters for getting task details."""

    task_id: str = Field(
        ...,
        description="ID of the task to retrieve (e.g., 'task-abc12345').",
        min_length=1,
    )


class TaskGetTool(Tool):
    """
    Retrieve full details for a specific task.

    Returns complete task information including:
    - Subject and description
    - Current status (pending/in_progress/completed)
    - Dependency relationships
    - Owner assignment
    - Metadata and timestamps
    - Availability status

    Use this before starting work on a task to understand:
    - Full requirements (description)
    - What must be done first (blocked_by)
    - What depends on this (blocks)
    """

    name = "task_get"
    description = """Retrieve full details for a specific task by ID.

Returns:
- subject, description, status
- blocked_by: tasks that must complete first
- blocks: tasks waiting on this one
- owner: assigned agent
- metadata: tracking data
- is_available: whether task can be started

Use before starting work to understand requirements and dependencies."""

    kind = ToolKind.READ
    schema = TaskGetParams

    def __init__(self, config: Any = None, task_manager: Any = None) -> None:
        super().__init__(config)
        self._task_manager = task_manager

    def set_task_manager(self, task_manager: Any) -> None:
        """Set the task manager (called by Session during initialization)."""
        self._task_manager = task_manager

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Get detailed task information."""
        params = TaskGetParams(**invocation.params)

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

        # Build detailed output
        status_icon = {"pending": "◼", "in_progress": "▶", "completed": "✔"}.get(task.status, "?")

        availability = (
            "Ready to start"
            if task.is_available()
            else ("Blocked" if task.is_blocked() else ("In progress" if task.status == "in_progress" else "Completed"))
        )

        output_lines = [
            f"{status_icon} Task #{task.id}",
            "",
            f"Subject: {task.subject}",
            f"Status: {task.status} ({availability})",
            "",
            "Description:",
            f"  {task.description}",
        ]

        if task.blocked_by:
            output_lines.append("")
            output_lines.append(f"Blocked by: {', '.join(task.blocked_by)}")

        if task.blocks:
            output_lines.append(f"Blocks: {', '.join(task.blocks)}")

        if task.owner:
            output_lines.append(f"Owner: {task.owner}")

        if task.metadata:
            output_lines.append(f"Metadata: {task.metadata}")

        output_lines.append("")
        output_lines.append(f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M')}")
        output_lines.append(f"Updated: {task.updated_at.strftime('%Y-%m-%d %H:%M')}")

        return ToolResult.success_result(
            output="\n".join(output_lines),
            metadata={
                "task_id": task.id,
                "subject": task.subject,
                "description": task.description,
                "status": task.status,
                "active_form": task.active_form,
                "owner": task.owner,
                "blocked_by": task.blocked_by,
                "blocks": task.blocks,
                "metadata": task.metadata,
                "is_available": task.is_available(),
                "is_blocked": task.is_blocked(),
                "created_at": task.created_at.isoformat(),
                "updated_at": task.updated_at.isoformat(),
            },
        )

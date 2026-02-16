"""
TaskList Tool - List all tasks with status and summary.

This tool provides an overview of all tasks:
- Grouped by status (in_progress, available, blocked, completed)
- Shows dependency relationships
- Includes summary statistics

Usage:
    task_list()
    task_list(filter_status="pending")
    task_list(include_completed=False)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class TaskListParams(BaseModel):
    """Parameters for listing tasks."""

    filter_status: Literal["pending", "in_progress", "completed"] | None = Field(
        None, description="Filter to only show tasks with this status."
    )

    include_completed: bool = Field(
        True,
        description="Whether to include completed tasks. Set false to focus on open work.",
    )

    show_descriptions: bool = Field(
        False,
        description="Whether to include task descriptions. Default shows only subjects.",
    )


class TaskListTool(Tool):
    """
    List all tasks with status summary.

    Shows tasks organized by status:
    - ▶ In Progress: Currently being worked on
    - ◼ Available: Ready to start (no blockers)
    - ⚠ Blocked: Waiting on dependencies
    - ✔ Completed: Finished tasks

    Includes summary statistics:
    - Total tasks
    - Completion count
    - Available count

    Use to find available work and track progress.
    """

    name = "task_list"
    description = """List all tasks with status and summary statistics.

Shows tasks grouped by status:
- ▶ In Progress: Currently being worked on
- ◼ Available: Ready to start (no blockers)
- ⚠ Blocked: Waiting on dependencies
- ✔ Completed: Finished tasks

Options:
- filter_status: Show only tasks with specific status
- include_completed: Show/hide completed tasks
- show_descriptions: Include task descriptions

Returns task counts and formatted list."""

    kind = ToolKind.READ
    schema = TaskListParams

    def __init__(self, config: Any = None, task_manager: Any = None) -> None:
        super().__init__(config)
        self._task_manager = task_manager

    def set_task_manager(self, task_manager: Any) -> None:
        """Set the task manager (called by Session during initialization)."""
        self._task_manager = task_manager

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """List all tasks with status summary."""
        params = TaskListParams(**invocation.params)

        # Get task manager from instance (injected by Session)
        task_manager = self._task_manager

        if task_manager is None:
            return ToolResult.error_result(
                error_message="No task manager available. Tasks not initialized.",
            )

        # Get all tasks
        all_tasks = list(task_manager.get_all_tasks())

        if not all_tasks:
            return ToolResult.success_result(
                output="No tasks found. Use task_create to add tasks.",
                metadata={
                    "total": 0,
                    "completed": 0,
                    "available": 0,
                    "blocked": 0,
                    "in_progress": 0,
                },
            )

        # Apply filters
        tasks = all_tasks
        if params.filter_status:
            tasks = [t for t in tasks if t.status == params.filter_status]
        if not params.include_completed:
            tasks = [t for t in tasks if t.status != "completed"]

        # Group by status
        in_progress = [t for t in tasks if t.status == "in_progress"]
        available = [t for t in tasks if t.is_available()]
        blocked = [t for t in tasks if t.is_blocked()]
        completed = [t for t in tasks if t.status == "completed"]

        # Build output
        output_lines = []

        # Summary line
        total = len(all_tasks)
        done = len([t for t in all_tasks if t.status == "completed"])
        output_lines.append(f"📋 Tasks: {done}/{total} completed\n")

        # In Progress section
        if in_progress:
            output_lines.append("▶ IN PROGRESS")
            for task in in_progress:
                line = f"  {task.id}: {task.subject}"
                if task.owner:
                    line += f" (owner: {task.owner})"
                output_lines.append(line)
                if params.show_descriptions:
                    output_lines.append(f"    {task.description[:100]}...")
            output_lines.append("")

        # Available section
        if available:
            output_lines.append("◼ AVAILABLE (Ready to start)")
            for task in available:
                output_lines.append(f"  {task.id}: {task.subject}")
                if params.show_descriptions:
                    output_lines.append(f"    {task.description[:100]}...")
            output_lines.append("")

        # Blocked section
        if blocked:
            output_lines.append("⚠ BLOCKED (Waiting on dependencies)")
            for task in blocked:
                blockers = ", ".join(task.blocked_by[:3])
                if len(task.blocked_by) > 3:
                    blockers += f" +{len(task.blocked_by) - 3} more"
                output_lines.append(f"  {task.id}: {task.subject}")
                output_lines.append(f"    ← waiting on: {blockers}")
            output_lines.append("")

        # Completed section (limited)
        if completed and params.include_completed:
            output_lines.append(f"✔ COMPLETED ({len(completed)} tasks)")
            # Show last 5 completed
            for task in completed[-5:]:
                output_lines.append(f"  {task.id}: {task.subject}")
            if len(completed) > 5:
                output_lines.append(f"  ... and {len(completed) - 5} more")
            output_lines.append("")

        # Stats summary
        stats = task_manager.get_stats()
        output_lines.append(
            f"Summary: {stats['in_progress']} in progress, {stats['available']} available, {stats['blocked']} blocked"
        )

        return ToolResult.success_result(
            output="\n".join(output_lines),
            metadata={
                "total": stats["total"],
                "completed": stats["completed"],
                "in_progress": stats["in_progress"],
                "available": stats["available"],
                "blocked": stats["blocked"],
                "pending": stats["pending"],
            },
        )

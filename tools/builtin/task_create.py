"""
TaskCreate Tool - Create new tasks with dependencies and metadata.

This tool creates structured tasks for complex, multi-step work:
- Multi-file features requiring coordination
- Large refactors spanning many files
- Work with dependencies (Task B needs Task A first)
- Parallel work across sub-agents

Usage:
    task_create(
        subject="Implement JWT authentication",
        description="Add JWT validation to API routes...",
        active_form="Implementing JWT auth",
        blocked_by=["task-setup-db"],
        metadata={"feature": "auth", "phase": "2.1"}
    )
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class TaskCreateParams(BaseModel):
    """Parameters for creating a new task."""

    subject: str = Field(
        ...,
        description="Brief imperative title (e.g., 'Implement JWT authentication', 'Add user validation', 'Fix login bug'). Use action verbs.",
        min_length=3,
        max_length=200,
    )

    description: str = Field(
        ...,
        description="Detailed requirements, acceptance criteria, and context. Be specific about what 'done' looks like.",
        min_length=10,
    )

    active_form: str | None = Field(
        None,
        description="Present-tense form shown in spinner (e.g., 'Implementing JWT authentication'). Auto-generated if not provided.",
    )

    blocked_by: list[str] | None = Field(
        None,
        description="List of task IDs that must complete before this task can start. Use for dependencies.",
    )

    blocks: list[str] | None = Field(
        None,
        description="List of task IDs that cannot start until this task completes. Inverse of blocked_by.",
    )

    metadata: dict[str, Any] | None = Field(
        None,
        description="Arbitrary key-value pairs for tracking (e.g., {feature: 'auth', phase: '2.1', priority: 'high'}).",
    )


class TaskCreateTool(Tool):
    """
    Create a new task with structured metadata and dependencies.

    The task system is designed for complex, multi-step work that benefits
    from tracking and coordination. Tasks support:

    - Dependencies: blocked_by/blocks for sequencing work
    - Metadata: Arbitrary tracking data
    - Cross-session: Tasks persist to disk
    - Multi-agent: Shared task lists for coordination
    """

    name = "task_create"
    description = """Create a new task with structured metadata for tracking complex work.

Use for multi-step work that benefits from tracking: multi-file features, refactors, dependent tasks.
Skip task creation for simple fixes (< 3 steps).

Parameters:
- subject: Brief imperative title ("Implement JWT authentication")
- description: Detailed requirements and acceptance criteria
- active_form: Present-tense spinner text ("Implementing JWT auth")
- blocked_by: Task IDs this depends on
- blocks: Task IDs that depend on this
- metadata: Key-value tracking data (feature, phase, priority)

Returns task_id for use with task_update, task_get."""

    kind = ToolKind.MEMORY
    schema = TaskCreateParams

    def __init__(self, config: Any = None, task_manager: Any = None) -> None:
        super().__init__(config)
        self._task_manager = task_manager

    def set_task_manager(self, task_manager: Any) -> None:
        """Set the task manager (called by Session during initialization)."""
        self._task_manager = task_manager

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Create a new task and add it to the session's task list."""
        params = TaskCreateParams(**invocation.params)

        # Get task manager from instance (injected by Session)
        task_manager = self._task_manager

        if task_manager is None:
            return ToolResult.error_result(
                error_message=("No task manager available. Cannot create task without persistence."),
                metadata={
                    "subject": params.subject,
                    "persisted": False,
                },
            )

        # Create task via manager (handles persistence and dependencies)
        try:
            task = task_manager.create_task(
                subject=params.subject,
                description=params.description,
                active_form=params.active_form,
                metadata=params.metadata,
                blocked_by=params.blocked_by,
                blocks=params.blocks,
            )
        except Exception as e:
            return ToolResult.error_result(
                error_message=f"Failed to create task: {e}",
                metadata={"error_type": type(e).__name__},
            )

        # Format success output
        blocked_str = ""
        if task.blocked_by:
            blocked_str = f"\n   Blocked by: {', '.join(task.blocked_by)}"

        blocks_str = ""
        if task.blocks:
            blocks_str = f"\n   Blocks: {', '.join(task.blocks)}"

        metadata_str = ""
        if task.metadata:
            metadata_str = f"\n   Metadata: {task.metadata}"

        return ToolResult.success_result(
            output=f"✔ Task #{task.id} created: {task.subject}\n"
            f"   Status: pending{blocked_str}{blocks_str}{metadata_str}",
            metadata={
                "task_id": task.id,
                "subject": task.subject,
                "status": task.status,
                "blocked_by": task.blocked_by,
                "blocks": task.blocks,
                "persisted": True,
            },
        )

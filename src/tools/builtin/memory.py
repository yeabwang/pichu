"""
Memory Tool - Agent tool for memory operations.

Commands:
    view    - View all stored memories
    save    - Save a new memory
    search  - Search memories by query
    forget  - Remove a memory

Usage in agent:
    {"command": "view"}
    {"command": "save", "content": "Project uses FastAPI", "category": "architecture"}
    {"command": "search", "query": "authentication"}
    {"command": "forget", "query": "old pattern"}
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from utils.memory_types import MemoryCategory

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

logger = logging.getLogger(__name__)


class MemoryParams(BaseModel):
    """Parameters for memory operations."""

    command: Literal["view", "save", "search", "forget"] = Field(..., description="The memory operation to perform")

    content: str | None = Field(None, description="(save) The memory content to store")

    category: (
        Literal[
            "architecture",
            "decision",
            "pattern",
            "gotcha",
            "progress",
            "context",
            "user_preference",
        ]
        | None
    ) = Field(None, description="(save/view) Category for the memory")

    tags: list[str] | None = Field(None, description="(save) Optional tags for better searchability")

    query: str | None = Field(None, description="(search/forget) Text to search for")

    is_global: bool = Field(False, description="(save) Store as global (cross-project) memory")

    memory_id: str | None = Field(None, description="(forget) Specific memory ID to remove")


class MemoryTool(Tool):
    """
    Tool for managing agent memory.

    Allows the agent to:
    - View all stored memories
    - Save new learnings (architecture, decisions, patterns, etc.)
    - Search for relevant context
    - Forget outdated information
    """

    name = "memory"
    description = """Manage persistent memory across sessions.

Commands:
- view: Show all stored memories (optionally filter by category)
- save: Store a new memory with category and optional tags
- search: Find memories matching a query
- forget: Remove memories by ID, query, or category

Memory Categories:
- architecture: System design, structure decisions (permanent)
- decision: Why specific choices were made (permanent)
- pattern: Code patterns and conventions (permanent)
- gotcha: Pitfalls and things to avoid (permanent)
- progress: Current work status (expires in 7 days)
- context: General project context (expires in 30 days)
- user_preference: User's coding preferences (permanent)

Use this to build project knowledge that persists across sessions."""

    kind = ToolKind.MEMORY
    schema = MemoryParams

    def __init__(self, config: Any = None, memory_manager: Any = None) -> None:
        super().__init__(config)
        self._memory_manager = memory_manager

    def set_memory_manager(self, manager: Any) -> None:
        """Set the memory manager (called by Session during initialization)."""
        self._memory_manager = manager

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        params = MemoryParams(**invocation.params)
        # Only mutating commands need confirmation
        if params.command in {"save", "forget"}:
            desc = (
                f"Save memory: {params.content[:80]}..."
                if params.command == "save" and params.content
                else f"Forget memory matching: {params.query or params.memory_id}"
            )
            return ToolConfirmation(
                tool_name=self.name,
                tool_kind=self.kind,
                params=invocation.params,
                description=desc,
            )
        return None

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute memory operation."""
        params = MemoryParams(**invocation.params)

        # Check for memory manager
        if self._memory_manager is None:
            return ToolResult.error_result(
                error_message="No memory manager available. Memory system not initialized.",
            )

        try:
            if params.command == "view":
                return await self._handle_view(params)
            elif params.command == "save":
                return await self._handle_save(params)
            elif params.command == "search":
                return await self._handle_search(params)
            elif params.command == "forget":
                return await self._handle_forget(params)
            else:
                return ToolResult.error_result(
                    error_message=f"Unknown command: {params.command}. Use: view, save, search, or forget",
                )
        except Exception as e:
            logger.exception(f"Memory tool error: {e}")
            return ToolResult.error_result(
                error_message=f"Memory operation failed: {str(e)}",
            )

    async def _handle_view(self, params: MemoryParams) -> ToolResult:
        """Handle view command - show all memories."""
        memories_by_category = self._memory_manager.view(
            category=params.category,
            include_global=params.is_global or True,
        )

        if not memories_by_category:
            return ToolResult.success_result(
                output="No memories stored yet.",
            )

        # Format output
        lines = ["📚 **Stored Memories**\n"]

        total = 0
        for cat_name, memories in memories_by_category.items():
            if not memories:
                continue

            lines.append(f"\n### {cat_name.title()}")
            for memory in memories:
                conf = f"({memory.confidence:.0%})" if memory.confidence < 1.0 else ""
                tags = f" [{', '.join(memory.tags)}]" if memory.tags else ""
                lines.append(f"- {memory.content}{tags} {conf}")
                total += 1

        lines.append(f"\n_Total: {total} memories_")

        return ToolResult.success_result(
            output="\n".join(lines),
            metadata={"total": total},
        )

    async def _handle_save(self, params: MemoryParams) -> ToolResult:
        """Handle save command - store a new memory."""
        if not params.content:
            return ToolResult.error_result(
                error_message="Missing required parameter: content",
            )

        category = params.category or "context"
        tags = params.tags or []

        # Validate category
        try:
            cat = MemoryCategory.from_string(category)
        except ValueError:
            return ToolResult.error_result(
                error_message=f"Invalid category: {category}",
            )

        # Save memory
        memory = self._memory_manager.save(
            content=params.content,
            category=cat,
            tags=tags,
            is_global=params.is_global,
            source="agent",
        )

        location = "global memory" if params.is_global else "project memory"
        return ToolResult.success_result(
            output=(
                f"✅ Memory saved to {location}\n"
                f"Category: {cat.emoji} {cat.value}\n"
                f"Content: {params.content[:100]}{'...' if len(params.content) > 100 else ''}\n"
                f"ID: {memory.id[:8]}..."
            ),
            metadata={"memory_id": memory.id, "category": cat.value},
        )

    async def _handle_search(self, params: MemoryParams) -> ToolResult:
        """Handle search command - find relevant memories."""
        if not params.query:
            return ToolResult.error_result(
                error_message="Missing required parameter: query",
            )

        results = self._memory_manager.search(
            query=params.query,
            category=params.category,
            tags=params.tags,
            limit=20,
        )

        if not results:
            return ToolResult.success_result(
                output=f"No memories found matching: {params.query}",
                metadata={"count": 0},
            )

        # Format results
        lines = [f"🔍 **Search results for:** {params.query}\n"]

        for i, memory in enumerate(results, 1):
            cat = memory.category
            conf = f"({memory.confidence:.0%})" if memory.confidence < 1.0 else ""
            lines.append(f"{i}. {cat.emoji} [{cat.value}] {memory.content} {conf}")

        lines.append(f"\n_Found {len(results)} matching memories_")

        return ToolResult.success_result(
            output="\n".join(lines),
            metadata={"count": len(results)},
        )

    async def _handle_forget(self, params: MemoryParams) -> ToolResult:
        """Handle forget command - remove memories."""
        if not any([params.memory_id, params.query, params.category]):
            return ToolResult.error_result(
                error_message="Must provide memory_id, query, or category to forget",
            )

        removed = self._memory_manager.forget(
            memory_id=params.memory_id,
            query=params.query,
            category=params.category,
        )

        if removed == 0:
            return ToolResult.success_result(
                output="No matching memories found to remove.",
                metadata={"removed": 0},
            )

        return ToolResult.success_result(
            output=f"🗑️ Removed {removed} memory(ies)",
            metadata={"removed": removed},
        )

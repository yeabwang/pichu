"""Data contracts for sub-agent configuration and execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SubAgentConfig:
    """
    Runtime configuration for one sub-agent definition.

    Attributes:
        name: Unique identifier (lowercase, hyphens). Used as tool name suffix.
        description: When this agent should be invoked. Used for auto-delegation
                     and as the tool description.
        system_prompt: The markdown body - guides the sub-agent's behavior.
        tools: List of allowed tool names/patterns. None means inherit all tools.
        disallowed_tools: List of tool names/patterns to explicitly deny.
        model: Model to use. None means inherit from main config.
               Special value "inherit" also means inherit from main config.
        permission_mode: How to handle permissions (default, acceptEdits,
                        dontAsk, bypassPermissions, delegate, plan).
        max_turns: Maximum reasoning/tool turns for this sub-agent.
        skills: Skills to preload into the sub-agent's context.
        hooks: Lifecycle hooks (PreToolUse, PostToolUse, Stop).
        color: UI indicator color for this sub-agent.
        source_path: Path to the original markdown file.
    """

    name: str
    description: str
    system_prompt: str
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None  # None or "inherit" = use main config
    permission_mode: str = "default"
    max_turns: int | None = None
    skills: list[str] | None = None
    hooks: dict[str, Any] | None = None
    color: str | None = None  # UI indicator color
    source_path: Path | None = None

    @property
    def tool_name(self) -> str:
        """Get the full tool name for this sub-agent."""
        return f"subagent_{self.name}"

    @property
    def should_inherit_model(self) -> bool:
        """Check if this sub-agent should inherit the main conversation's model."""
        return self.model is None or self.model.lower() == "inherit"

    def get_effective_model(self, fallback_model: str) -> str:
        """
        Get the effective model to use for this sub-agent.

        Args:
            fallback_model: The model from main config to use if not specified.

        Returns:
            The model identifier to use.
        """
        if self.should_inherit_model:
            return fallback_model
        model = self.model
        if model is None:
            return fallback_model
        return model

    def get_turn_limit(self, default_turn_limit: int) -> int:
        """Resolve effective max turns with validation and fallback."""
        if self.max_turns is None:
            return default_turn_limit
        if self.max_turns <= 0:
            return default_turn_limit
        return self.max_turns

    def __repr__(self) -> str:
        return f"SubAgentConfig(name={self.name!r}, model={self.model!r}, tools={self.tools!r})"


@dataclass
class SubAgentInvocation:
    """
    Represents an invocation of a sub-agent.

    Attributes:
        config: The sub-agent configuration.
        prompt: The task/question for the sub-agent.
        context: Additional context to provide.
        cwd: Current working directory for tool execution.
    """

    config: SubAgentConfig
    prompt: str
    context: str = ""
    cwd: Path = field(default_factory=Path.cwd)

    @property
    def full_prompt(self) -> str:
        """Get the complete prompt including context."""
        if self.context:
            return f"{self.context}\n\n{self.prompt}"
        return self.prompt


@dataclass
class SubAgentResult:
    """
    Result from a sub-agent execution.

    Attributes:
        success: Whether the sub-agent completed successfully.
        output: The final response from the sub-agent.
        error: Error message if failed.
        turns_used: Number of turns the sub-agent took.
        agent_id: Unique ID for resuming this agent later.
    """

    success: bool
    output: str
    error: str | None = None
    turns_used: int = 0
    agent_id: str | None = None

    @classmethod
    def from_success(cls, output: str, turns: int = 0, agent_id: str | None = None) -> SubAgentResult:
        """Create a successful result."""
        return cls(success=True, output=output, turns_used=turns, agent_id=agent_id)

    @classmethod
    def from_error(cls, error: str, output: str = "") -> SubAgentResult:
        """Create an error result."""
        return cls(success=False, output=output, error=error)

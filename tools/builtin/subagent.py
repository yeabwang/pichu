"""Sub-agent orchestration tool for delegated, isolated agent execution."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from utils.subagent_transcript import SubAgentTranscript
from utils.subagent_types import SubAgentConfig, SubAgentResult

if TYPE_CHECKING:
    from config import Config
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SubAgentParams(BaseModel):
    """Parameters for invoking a sub-agent."""

    prompt: str = Field(
        ...,
        description="The task or question to delegate to this sub-agent.",
    )
    context: str = Field(
        default="",
        description="Additional context to provide to the sub-agent (optional).",
    )
    resume: str | None = Field(
        default=None,
        description="Agent ID to resume a previous sub-agent conversation.",
    )
    run_in_background: bool = Field(
        default=False,
        description="Run the sub-agent in the background without blocking.",
    )


class SubAgentTool(Tool):
    """
    A tool that wraps a sub-agent for execution.

    When invoked, this tool:
    1. Creates an isolated conversation with the sub-agent's system prompt
    2. Filters tools based on the sub-agent's configuration
    3. Runs an agent loop until the sub-agent completes
    4. Returns the final response to the main agent

    Key features:
    - Context isolation: Sub-agent runs in its own conversation
    - Tool restriction: Can limit which tools the sub-agent can use
    - Model selection: Can use a different model than the main agent
    - No nesting: Sub-agents cannot invoke other sub-agents
    """

    kind = ToolKind.READ  # Sub-agents don't directly mutate

    # Default maximum iterations when sub-agent config does not set max_turns.
    DEFAULT_MAX_ITERATIONS = 30

    _background_tasks: dict[str, asyncio.Task[SubAgentResult]] = {}

    def __init__(
        self,
        config: SubAgentConfig,
        llm_config: Any,
        tool_registry: ToolRegistry,
        main_config: Config,
        fallback_model: str | None = None,
        session_id: str | None = None,
        skill_loader: Callable[[str], str | None] | None = None,
    ):
        """
        Initialize a sub-agent tool.

        Args:
            config: The sub-agent configuration.
            llm_config: The LLM config for creating sub-agent clients.
            tool_registry: The tool registry containing all available tools.
            main_config: The main application config.
            fallback_model: Model to use when sub-agent doesn't specify one.
            session_id: Session ID for transcript storage.
            skill_loader: Optional function to load skill content by name.
        """
        super().__init__(main_config)

        self._subagent_config = config
        self._llm_config = llm_config
        self._tool_registry = tool_registry
        self._main_config = main_config
        self._fallback_model = fallback_model or (llm_config.model if hasattr(llm_config, "model") else None)
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._skill_loader = skill_loader

        # Preload skills content once per tool instance.
        self._preloaded_skills: dict[str, str] = {}
        self._load_skills()

    def _load_skills(self) -> None:
        """Preload skill content for skills specified in config."""
        if not self._subagent_config.skills or not self._skill_loader:
            return

        for skill_name in self._subagent_config.skills:
            try:
                content = self._skill_loader(skill_name)
                if content:
                    self._preloaded_skills[skill_name] = content
                    logger.debug(f"Preloaded skill '{skill_name}' for sub-agent")
            except Exception as e:
                logger.warning(f"Failed to load skill '{skill_name}': {e}")

    @property
    def name(self) -> str:  # type: ignore[override]
        """Tool name is prefixed with 'subagent_'."""
        return self._subagent_config.tool_name

    @property
    def description(self) -> str:  # type: ignore[override]
        """Use the sub-agent's description."""
        return self._subagent_config.description

    @property
    def schema(self) -> type[BaseModel]:
        """Parameter schema for this tool."""
        return SubAgentParams

    def get_effective_model(self) -> str:
        """
        Get the model to use for this sub-agent.

        Falls back to fallback_model if not specified or set to 'inherit'.
        """
        if self._fallback_model:
            return self._subagent_config.get_effective_model(self._fallback_model)
        # Try to get from llm_config
        if hasattr(self._llm_config, "model") and self._llm_config.model:
            return self._subagent_config.get_effective_model(self._llm_config.model)
        # Last resort - return whatever the sub-agent specifies or a default
        return self._subagent_config.model or "gpt-4.1"

    def get_allowed_tools(self) -> list[Tool]:
        """
        Get the tools that this sub-agent is allowed to use.

        Rules:
        1. Never allow sub-agent tools (prevents nesting)
        2. If tools list is specified, only allow those
        3. If tools list is None, inherit all (non-subagent) tools
        4. Always respect disallowed_tools
        """
        return self._tool_registry.select_tools(
            allow=self._subagent_config.tools,
            deny=self._subagent_config.disallowed_tools,
            include_subagents=False,
            include_mcp=True,
        )

    async def _run_hooks(
        self,
        event: str,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Run lifecycle hooks for this sub-agent.

        Args:
            event: Hook event type (PreToolUse, PostToolUse, Stop)
            tool_name: Name of the tool (for tool hooks)
            tool_input: Tool input parameters
            tool_output: Tool output (for PostToolUse)

        Returns:
            Tuple of (should_continue, error_message)
            For PreToolUse, should_continue=False means block the tool call.
        """
        hooks = self._subagent_config.hooks
        if not isinstance(hooks, dict):
            return (True, None)

        event_hooks = hooks.get(event)
        if not isinstance(event_hooks, list) or not event_hooks:
            return (True, None)

        for hook_config in event_hooks:
            if not isinstance(hook_config, dict):
                continue

            # Check matcher
            matcher = hook_config.get("matcher")
            if matcher and tool_name:
                if not re.match(matcher, tool_name):
                    continue

            for hook in hook_config.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                hook_type = hook.get("type")

                if hook_type == "command":
                    command = str(hook.get("command", "")).strip()
                    if not command:
                        continue

                    # Prepare hook input as JSON
                    hook_input = {
                        "event": event,
                        "subagent_name": self._subagent_config.name,
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_output": tool_output,
                    }

                    return_code, stderr = await self._run_hook_command(
                        command=command,
                        payload=hook_input,
                    )
                    if return_code == 2:
                        error_msg = stderr or "Operation blocked by hook"
                        logger.info(f"Hook blocked operation: {error_msg}")
                        return (False, error_msg)
                    if return_code != 0:
                        logger.warning(f"Hook command exited with code {return_code}: {stderr}")

        return (True, None)

    async def _run_hook_command(
        self,
        command: str,
        payload: dict[str, Any],
        timeout_seconds: int = 30,
    ) -> tuple[int, str]:
        """Execute one hook command without blocking the event loop."""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=(str(self._main_config.cwd) if hasattr(self._main_config, "cwd") else None),
            )
            _, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=json.dumps(payload).encode("utf-8")),
                timeout=timeout_seconds,
            )
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return (process.returncode or 0, stderr_text)
        except asyncio.TimeoutError:
            logger.warning(f"Hook command timed out: {command}")
            return (124, "hook command timed out")
        except Exception as exc:
            logger.warning(f"Hook command failed: {exc}")
            return (1, str(exc))

    def _check_permission_mode(self, tool: Tool) -> tuple[bool, str | None]:
        """
        Check if tool execution is allowed based on permission mode.

        Args:
            tool: The tool to check.

        Returns:
            Tuple of (allowed, reason)
        """
        mode = self._subagent_config.permission_mode

        if mode == "bypassPermissions":
            # Allow everything
            return (True, None)

        if mode == "plan":
            # Only allow read-only tools
            if tool.kind == ToolKind.WRITE:
                return (False, "Write operations not allowed in plan mode")
            return (True, None)

        if mode == "delegate":
            return (False, "Tool execution is disabled in delegate mode")

        if mode == "dontAsk":
            # Auto-deny permission prompts (but explicitly allowed tools still work)
            # In this implementation, we allow tools in the tools list
            return (True, None)

        if mode == "acceptEdits":
            # Auto-accept file edits
            return (True, None)

        # default mode - standard permission checking
        return (True, None)

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """
        Execute the sub-agent.

        Supports:
        - Normal foreground execution
        - Resume from previous conversation
        - Background execution (non-blocking)
        """
        params = SubAgentParams(**invocation.params)

        # Check if resuming a previous agent
        if params.resume:
            agent_id = params.resume
            logger.info(f"Resuming sub-agent '{self._subagent_config.name}' (id: {agent_id})")
        else:
            agent_id = str(uuid.uuid4())[:8]
            logger.info(f"Starting sub-agent '{self._subagent_config.name}' (id: {agent_id})")

        # Handle background execution
        if params.run_in_background:
            return await self._execute_background(
                params=params,
                agent_id=agent_id,
                cwd=invocation.cwd,
            )

        # Foreground execution
        try:
            result = await self._run_subagent(
                prompt=params.prompt,
                context=params.context,
                cwd=invocation.cwd,
                agent_id=agent_id,
                resume=params.resume is not None,
            )

            if result.success:
                return ToolResult.success_result(
                    output=result.output,
                    metadata={
                        "agent_id": result.agent_id,
                        "turns_used": result.turns_used,
                        "subagent_name": self._subagent_config.name,
                        "color": self._subagent_config.color,
                    },
                )
            else:
                return ToolResult.error_result(
                    error_message=result.error or "Sub-agent execution failed",
                    output=result.output,
                )

        except Exception as e:
            logger.exception(f"Sub-agent '{self._subagent_config.name}' failed")
            return ToolResult.error_result(
                error_message=f"Sub-agent execution error: {str(e)}",
            )

    async def _execute_background(
        self,
        params: SubAgentParams,
        agent_id: str,
        cwd: Path,
    ) -> ToolResult:
        """
        Execute sub-agent in background without blocking.

        Returns immediately with task ID for later status check.
        """
        self._prune_background_tasks()
        task_id = f"task-{agent_id}"

        async def background_task():
            try:
                return await self._run_subagent(
                    prompt=params.prompt,
                    context=params.context,
                    cwd=cwd,
                    agent_id=agent_id,
                    resume=params.resume is not None,
                    is_background=True,
                )
            except Exception as e:
                logger.exception(f"Background sub-agent failed: {e}")
                return SubAgentResult.from_error(str(e))

        # Start the background task
        task = asyncio.create_task(background_task())
        SubAgentTool._background_tasks[task_id] = task

        logger.info(f"Started background sub-agent task: {task_id}")

        return ToolResult.success_result(
            output=f"Sub-agent started in background.\n\nTask ID: {task_id}\nAgent ID: {agent_id}\n\nUse TaskOutput tool to check status.",
            metadata={
                "task_id": task_id,
                "agent_id": agent_id,
                "status": "running",
                "subagent_name": self._subagent_config.name,
            },
        )

    @classmethod
    def _prune_background_tasks(cls, keep_task_id: str | None = None) -> None:
        """Drop completed tasks from the in-memory background task index."""
        completed = [
            task_id for task_id, task in cls._background_tasks.items() if task.done() and task_id != keep_task_id
        ]
        for task_id in completed:
            cls._background_tasks.pop(task_id, None)

    @classmethod
    def get_background_task_status(cls, task_id: str) -> dict[str, Any]:
        """
        Get status of a background sub-agent task.

        Args:
            task_id: The task ID returned when starting background execution.

        Returns:
            Status dict with status, result (if complete), error (if failed).
        """
        cls._prune_background_tasks(keep_task_id=task_id)
        if task_id not in cls._background_tasks:
            return {"status": "not_found", "error": f"Task {task_id} not found"}

        task = cls._background_tasks[task_id]

        if not task.done():
            return {"status": "running"}

        cls._background_tasks.pop(task_id, None)
        try:
            result = task.result()
            return {
                "status": "completed" if result.success else "failed",
                "result": result.output,
                "error": result.error,
                "agent_id": result.agent_id,
                "turns_used": result.turns_used,
            }
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    async def _run_subagent(
        self,
        prompt: str,
        context: str,
        cwd: Path,
        agent_id: str,
        resume: bool = False,
        is_background: bool = False,
    ) -> SubAgentResult:
        """
        Run the sub-agent loop.

        This is similar to the main agent loop but:
        - Uses the sub-agent's system prompt
        - Uses restricted tools
        - May use a different model
        - Runs in complete isolation
        - Persists transcript for resumption
        - Executes lifecycle hooks
        """
        from client.models import StreamEventType, ToolCall

        # Initialize transcript
        transcript = SubAgentTranscript(
            agent_id=agent_id,
            session_id=self._session_id,
            subagent_name=self._subagent_config.name,
        )

        # Build the system message
        system_prompt = self._build_system_prompt()

        # Handle resume or new conversation
        if resume:
            # Load existing conversation
            messages = transcript.load()
            if messages:
                messages = transcript.to_conversation_messages()
                logger.info(f"Resumed conversation with {len(messages)} messages")
                # Disable recording during resume to avoid duplicates
                transcript.disable_recording()
            else:
                # No existing transcript, start fresh
                resume = False

        if not resume:
            # Start new conversation
            messages = [
                {"role": "system", "content": system_prompt},
            ]

            # Record initial messages
            transcript.append_metadata(
                "start",
                {
                    "subagent_name": self._subagent_config.name,
                    "model": self.get_effective_model(),
                    "is_background": is_background,
                },
            )
            transcript.append_system(system_prompt)

        # Add the new user prompt
        user_content = f"{context}\n\n{prompt}" if context else prompt
        messages.append({"role": "user", "content": user_content})
        transcript.enable_recording()  # Re-enable if was disabled
        transcript.append_user(user_content)

        # Get allowed tools
        allowed_tools = self.get_allowed_tools()
        tools_schema = [t.to_openai_schema() for t in allowed_tools]
        tools_by_name = {tool.name: tool for tool in allowed_tools}

        if self._main_config.debug:
            logger.debug(
                f"Sub-agent '{self._subagent_config.name}' has access to "
                f"{len(allowed_tools)} tools: {[t.name for t in allowed_tools]}"
            )

        # Get the effective model
        effective_model = self.get_effective_model()
        turn_limit = self._subagent_config.get_turn_limit(self.DEFAULT_MAX_ITERATIONS)

        if self._main_config.debug:
            logger.debug(f"Sub-agent '{self._subagent_config.name}' using model: {effective_model}")

        # Create a temporary LLM config with the sub-agent's model
        from config import LLMConfig

        subagent_llm_config = LLMConfig(
            api_key=self._main_config.llm.api_key,
            base_url=self._main_config.llm.base_url,
            model=effective_model,
            temperature=self._main_config.llm.temperature,
            timeout=self._main_config.llm.timeout,
            max_tokens=self._main_config.llm.max_tokens,
            retry=self._main_config.llm.retry,
        )

        # Create a new LLM client for this sub-agent
        from client.llm_client import LLMClient

        subagent_client = LLMClient(subagent_llm_config)

        turns_used = 0
        final_response = ""

        try:
            for turn in range(turn_limit):
                turns_used = turn + 1

                if self._main_config.debug:
                    logger.debug(f"Sub-agent '{self._subagent_config.name}' turn {turns_used}/{turn_limit}")

                # Make LLM call
                response_text = ""
                tool_calls: list[ToolCall] = []

                async for event in subagent_client.chat_completion(
                    messages=messages,
                    tools=tools_schema if tools_schema else None,
                    stream=True,
                ):
                    if event.type == StreamEventType.TEXT_DELTA:
                        content = event.text_delta.content if event.text_delta else ""
                        response_text += content
                    elif event.type == StreamEventType.TOOL_CALL_COMPLETE:
                        if event.tool_call:
                            tool_calls.append(event.tool_call)
                    elif event.type == StreamEventType.ERROR:
                        return SubAgentResult.from_error(
                            error=event.error or "LLM API error",
                            output=response_text,
                        )

                # Add assistant message to conversation and transcript
                if tool_calls:
                    tool_calls_data = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in tool_calls
                    ]
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response_text or None,
                            "tool_calls": tool_calls_data,
                        }
                    )
                    transcript.append_assistant(response_text or None, tool_calls_data)
                else:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response_text,
                        }
                    )
                    transcript.append_assistant(response_text)

                final_response = response_text

                # If no tool calls, we're done
                if not tool_calls:
                    # Run Stop hooks
                    await self._run_hooks("Stop")

                    logger.info(f"Sub-agent '{self._subagent_config.name}' completed in {turns_used} turns")

                    transcript.append_metadata(
                        "stop",
                        {
                            "turns_used": turns_used,
                            "success": True,
                        },
                    )

                    return SubAgentResult.from_success(
                        output=final_response,
                        turns=turns_used,
                        agent_id=agent_id,
                    )

                # Execute tool calls
                for tool_call in tool_calls:
                    # Find the tool
                    tool = tools_by_name.get(tool_call.name)

                    if tool is None:
                        # Tool not found or not allowed
                        tool_result = f"Error: Tool '{tool_call.name}' not available"
                    else:
                        # Check permission mode
                        allowed, reason = self._check_permission_mode(tool)
                        if not allowed:
                            tool_result = f"Permission denied: {reason}"
                        else:
                            # Run PreToolUse hooks
                            should_continue, hook_error = await self._run_hooks(
                                "PreToolUse",
                                tool_name=tool_call.name,
                                tool_input=tool_call.arguments,
                            )

                            if not should_continue:
                                tool_result = f"Blocked by hook: {hook_error}"
                            else:
                                # Execute the tool
                                try:
                                    result = await tool.execute(ToolInvocation(cwd=cwd, params=tool_call.arguments))
                                    tool_result = result.to_model_output()

                                    # Run PostToolUse hooks
                                    await self._run_hooks(
                                        "PostToolUse",
                                        tool_name=tool_call.name,
                                        tool_input=tool_call.arguments,
                                        tool_output=tool_result,
                                    )
                                except Exception as e:
                                    tool_result = f"Error executing tool: {str(e)}"
                                    await self._run_hooks(
                                        "PostToolUseFailure",
                                        tool_name=tool_call.name,
                                        tool_input=tool_call.arguments,
                                        tool_output=tool_result,
                                    )

                    # Add tool result to conversation and transcript
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
                    transcript.append_tool_result(tool_call.id, tool_result)

            # Exceeded max iterations
            logger.warning(f"Sub-agent '{self._subagent_config.name}' reached maximum iterations ({turn_limit})")

            # Run Stop hooks
            await self._run_hooks("Stop")

            transcript.append_metadata(
                "stop",
                {
                    "turns_used": turns_used,
                    "success": True,
                    "max_iterations_reached": True,
                },
            )

            return SubAgentResult.from_success(
                output=final_response + "\n\n[Sub-agent reached maximum iterations]",
                turns=turns_used,
                agent_id=agent_id,
            )

        finally:
            # Clean up the sub-agent's LLM client
            await subagent_client.close()

    def _build_system_prompt(self) -> str:
        """
        Build the system prompt for this sub-agent.

        The system prompt includes:
        - The markdown body from the sub-agent file
        - Preloaded skills content
        - Basic environment context
        """
        parts = []

        # Main system prompt
        parts.append(self._subagent_config.system_prompt)

        # Add preloaded skills
        if self._preloaded_skills:
            parts.append("\n\n---\n## Preloaded Skills\n")
            for skill_name, skill_content in self._preloaded_skills.items():
                parts.append(f"\n### {skill_name}\n{skill_content}\n")

        # Add minimal environment context
        cwd = self._main_config.cwd if hasattr(self._main_config, "cwd") else "."
        parts.append(f"\n\n---\nWorking directory: {cwd}\n")

        return "".join(parts)


def create_subagent_tools(
    subagents: list[SubAgentConfig] | dict[str, SubAgentConfig],
    llm_config: Any,
    tool_registry: ToolRegistry,
    config: Config,
    fallback_model: str | None = None,
    session_id: str | None = None,
    skill_loader: Callable[[str], str | None] | None = None,
) -> list[SubAgentTool]:
    """
    Create SubAgentTool instances for all loaded sub-agent configs.

    Args:
        subagents: List or dictionary of sub-agent configurations.
        llm_config: The LLM configuration (for creating sub-agent clients).
        tool_registry: The tool registry.
        config: The main application config.
        fallback_model: Model to use when sub-agent doesn't specify one.
        session_id: Session ID for transcript storage.
        skill_loader: Optional function to load skill content by name.

    Returns:
        List of SubAgentTool instances.
    """
    tools = []

    # Normalize to list
    if isinstance(subagents, dict):
        configs = list(subagents.values())
    else:
        configs = subagents

    for agent_config in configs:
        try:
            tool = SubAgentTool(
                config=agent_config,
                llm_config=llm_config,
                tool_registry=tool_registry,
                main_config=config,
                fallback_model=fallback_model,
                session_id=session_id,
                skill_loader=skill_loader,
            )
            tools.append(tool)
            logger.debug(f"Created sub-agent tool: {tool.name}")
        except Exception as e:
            logger.warning(f"Failed to create sub-agent tool for '{agent_config.name}': {e}")

    return tools

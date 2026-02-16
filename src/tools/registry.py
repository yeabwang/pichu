import fnmatch
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from safety.approval import ApprovalDecision, ApprovalManager
from utils.runtime_logging import audit_event

from tools.base import Tool, ToolInvocation, ToolResult
from tools.builtin import get_all_builtin_tools

if TYPE_CHECKING:
    from hooks.engine import HookEngine

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._mcp_tools: dict[str, Tool] = {}

    def register_tool(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.error(f"Overwrite attempt for tool: {tool.name}")
            raise ValueError(f"Overwrite attempt for tool: {tool.name}")
        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def register_mcp_tool(self, tool: Tool) -> None:
        self._mcp_tools[tool.name] = tool
        logger.debug(f"Registered MCP tool: {tool.name}")

    def clear_mcp_tools(self) -> None:
        self._mcp_tools.clear()

    def get_mcp_tool_names(self) -> list[str]:
        return list(self._mcp_tools.keys())

    @staticmethod
    def normalize_tool_name(name: str) -> str:
        """Normalize user-facing tool names for resilient matching."""
        return name.casefold().replace("_", "").replace("-", "")

    def list_tools(self, include_mcp: bool = True) -> list[Tool]:
        """Return registered tools in deterministic registration order."""
        tools = list(self._tools.values())
        if include_mcp:
            tools.extend(self._mcp_tools.values())
        return tools

    def select_tools(
        self,
        *,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        include_subagents: bool = True,
        include_mcp: bool = True,
    ) -> list[Tool]:
        """Select tools by allow/deny patterns using normalized matching.

        Matching is case-insensitive and tolerant of `-`/`_` differences. Both
        raw and normalized names are checked so callers can use human-friendly
        names (`ReadFile`, `read_file`, `read-file`) and simple globs.
        """
        candidates = self.list_tools(include_mcp=include_mcp)
        if not include_subagents:
            candidates = [t for t in candidates if not t.name.startswith("subagent_")]

        if allow:
            candidates = [t for t in candidates if self._matches_patterns(t.name, allow)]
        if deny:
            candidates = [t for t in candidates if not self._matches_patterns(t.name, deny)]
        return candidates

    def _matches_patterns(self, tool_name: str, patterns: list[str]) -> bool:
        raw_name = tool_name.casefold()
        normalized_name = self.normalize_tool_name(tool_name)
        for pattern in patterns:
            raw_pattern = pattern.casefold()
            normalized_pattern = self.normalize_tool_name(pattern)

            if raw_name == raw_pattern or normalized_name == normalized_pattern:
                return True

            if fnmatch.fnmatch(raw_name, raw_pattern):
                return True
            if fnmatch.fnmatch(normalized_name, normalized_pattern):
                return True
        return False

    def get_tools(self, name: str | None = None) -> list[Tool]:
        tools = self.list_tools(include_mcp=True)
        if name is None:
            return tools

        filtered: list[Tool] = []
        for tool in self.list_tools(include_mcp=True):
            tool_name = tool.name
            if name in tool_name:
                filtered.append(tool)

        return filtered

    def get(self, name: str) -> Tool | None:
        if name in self._tools:
            return self._tools[name]
        elif name in self._mcp_tools:
            return self._mcp_tools[name]
        else:
            return None

    def unregister_tool(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            logger.debug(f"Unregistered tool: {name}")
            return True
        else:
            logger.warning(f"Attempted to unregister non-existent tool: {name}")
            return False

    def get_schema(self) -> list[dict[str, dict]]:
        return [tool.to_openai_schema() for tool in self.get_tools()]

    async def invoke(
        self,
        name: str,
        params: dict[str, str],
        cwd: Path,
        approval_manager: ApprovalManager | None = None,
        hook_engine: "HookEngine | None" = None,
    ) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            logger.error(f"Tool not found: {name}")
            return ToolResult.error_result(f"Tool not found: {name}", metadata={"tool_name": name})

        validation_errors = tool.validate_params(params)
        if validation_errors:
            error_message = " | ".join(validation_errors)
            logger.error(f"Parameter validation failed for tool {name}: {error_message}")
            return ToolResult.error_result(
                f"Parameter validation failed for tool {name}: {error_message}",
                metadata={
                    "tool_name": name,
                    "validation_errors": validation_errors,
                },
            )

        invocation = ToolInvocation(
            params=params,
            cwd=cwd,
        )

        # --- PreToolUse hooks ---
        if hook_engine and hook_engine.has_hooks:
            from hooks.types import HookEvent

            pre_result = await hook_engine.fire(
                HookEvent.PRE_TOOL_USE,
                match_value=name,
                extra={
                    "tool_name": name,
                    "tool_input": params,
                },
            )

            # Check for stop signal
            if pre_result.should_stop:
                audit_event(
                    "tool.blocked_by_hook",
                    "Tool execution stopped by hook",
                    tool_name=name,
                    reason=pre_result.stop_reason or "hook requested stop",
                )
                return ToolResult.error_result(
                    f"Hook stopped execution: {pre_result.stop_reason or 'hook requested stop'}",
                    metadata={"tool_name": name, "reason": "hook_stop"},
                )

            # Check for deny decision (exit code 2 or permissionDecision=deny)
            if pre_result.any_deny or pre_result.any_blocking:
                reason = pre_result.blocking_reason or "blocked by PreToolUse hook"
                logger.info(f"Tool '{name}' blocked by hook: {reason}")
                audit_event(
                    "tool.blocked_by_hook",
                    "Tool execution blocked by PreToolUse hook",
                    tool_name=name,
                    reason=reason,
                )
                return ToolResult.error_result(
                    f"Tool '{name}' was blocked by hook: {reason}",
                    metadata={"tool_name": name, "reason": "hook_denied"},
                )

            # Check for allow decision (bypasses approval system)
            hook_bypasses_approval = pre_result.any_allow

            # Check for input modification
            updated_input = pre_result.first_updated_input
            if updated_input:
                params = {**params, **updated_input}
                invocation = ToolInvocation(params=params, cwd=cwd)
                logger.debug(f"Hook modified tool input for '{name}'")
        else:
            hook_bypasses_approval = False

        # --- Approval check (skipped if hook already allowed) ---
        if not hook_bypasses_approval and approval_manager and tool.is_mutating(params):
            confirmation = await tool.get_confirmation(invocation)
            if confirmation:
                decision = await approval_manager.check_and_approve(confirmation)
                if decision == ApprovalDecision.REJECTED:
                    reason = "denied by policy" if not confirmation.is_dangerous else "dangerous command blocked"
                    logger.info(f"Tool '{name}' execution denied: {reason}")
                    return ToolResult.error_result(
                        f"Tool '{name}' was denied: {reason}.",
                        metadata={"tool_name": name, "reason": reason},
                    )

        # --- Execute tool ---
        try:
            result = await tool.execute(invocation)
        except Exception as e:
            logger.error(f"Error executing tool {name}: {str(e)}")
            result = ToolResult.error_result(
                f"Error executing tool {name}: {str(e)}",
                metadata={"tool_name": name},
            )

            # --- PostToolUseFailure hooks ---
            if hook_engine and hook_engine.has_hooks:
                from hooks.types import HookEvent

                await hook_engine.fire(
                    HookEvent.POST_TOOL_USE_FAILURE,
                    match_value=name,
                    extra={
                        "tool_name": name,
                        "tool_input": params,
                        "error": str(e),
                        "is_interrupt": False,
                    },
                )

            return result

        # --- PostToolUse hooks (on success) ---
        if hook_engine and hook_engine.has_hooks and result.success:
            from hooks.types import HookEvent

            post_result = await hook_engine.fire(
                HookEvent.POST_TOOL_USE,
                match_value=name,
                extra={
                    "tool_name": name,
                    "tool_input": params,
                    "tool_response": {
                        "success": result.success,
                        "output": result.output[:2000] if result.output else "",
                        "filePath": params.get("file_path") or params.get("path", ""),
                    },
                },
            )

            # PostToolUse can provide feedback via decision="block"
            if post_result.should_block:
                reason = post_result.blocking_reason or "PostToolUse hook feedback"
                # Don't actually block (tool already ran), but add feedback to output
                feedback = f"\n[Hook feedback: {reason}]"
                if result.output:
                    result.output += feedback
                else:
                    result.output = feedback

            # suppressOutput: signal TUI to hide this tool's output
            if post_result.any_suppress_output:
                if result.metadata is None:
                    result.metadata = {}
                result.metadata["suppress_output"] = True

        # --- PostToolUseFailure hooks (on failure result, not exception) ---
        elif hook_engine and hook_engine.has_hooks and not result.success:
            from hooks.types import HookEvent

            await hook_engine.fire(
                HookEvent.POST_TOOL_USE_FAILURE,
                match_value=name,
                extra={
                    "tool_name": name,
                    "tool_input": params,
                    "error": result.error or "Unknown error",
                    "is_interrupt": False,
                },
            )

        # --- SubagentStop hook (fires when a sub-agent tool completes) ---
        if hook_engine and hook_engine.has_hooks and name.startswith("subagent_"):
            from hooks.types import HookEvent

            await hook_engine.fire(
                HookEvent.SUBAGENT_STOP,
                extra={
                    "tool_name": name,
                    "subagent_name": (result.metadata or {}).get("subagent_name", name),
                    "success": result.success,
                    "output": result.output[:2000] if result.output else "",
                    "agent_id": (result.metadata or {}).get("agent_id", ""),
                    "turns_used": (result.metadata or {}).get("turns_used", 0),
                },
            )

        return result


def create_default_tool_registry(config=None) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in get_all_builtin_tools(config):
        registry.register_tool(tool)

    return registry

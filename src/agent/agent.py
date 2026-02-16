"""Runtime orchestration for a single interactive agent session."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, AsyncGenerator

from client.models import StreamEventType, TokenUsage, ToolCall, ToolResultMessage
from config import Config
from hooks.types import HookEvent
from utils.loop_detector import (
    LoopAction,
    LoopDetectionConfig,
    LoopDetector,
    TurnRecord,
    _hash_args,
    _hash_text,
)
from utils.text import count_tokens

from agent.events import AgentEvent, AgentEventType
from agent.session import Session

if TYPE_CHECKING:
    from context.context_manager import ContextManager

logger = logging.getLogger(__name__)


class Agent:
    """Coordinates prompt handling, tool execution, and lifecycle hooks."""

    def __init__(self, config: Config):
        """Create an agent with a fresh runtime session and loop detector."""
        self._config = config
        self.session: Session | None = Session(self._config)

        loop_cfg = getattr(config, "loop_detection", None)
        if loop_cfg and isinstance(loop_cfg, LoopDetectionConfig):
            self._loop_detector = LoopDetector(loop_cfg)
        else:
            from config.config import LoopDetectionConfig as LDConfig

            ld = getattr(config, "loop_detection", LDConfig())
            self._loop_detector = LoopDetector(
                LoopDetectionConfig(
                    enabled=ld.enabled,
                    max_repeated_tool_calls=ld.max_repeated_tool_calls,
                    max_repeated_errors=ld.max_repeated_errors,
                    max_similar_responses=ld.max_similar_responses,
                    similarity_threshold=ld.similarity_threshold,
                    window_size=ld.window_size,
                    action=ld.action,
                )
            )

        if self._config.debug:
            registered = [t.name for t in self.session.tool_registry.get_tools()]
            logger.debug(f"Registered tools: {registered}")

    def _require_session(self) -> Session:
        """Return the active session or raise if the agent has been closed."""
        if self.session is None:
            raise RuntimeError("Session is not initialized.")
        return self.session

    def _require_context(self) -> "ContextManager":
        """Return the active context manager or raise."""
        session = self._require_session()
        if session.context_manager is None:
            raise RuntimeError("Context manager is not initialized.")
        return session.context_manager

    def _apply_usage(self, usage: TokenUsage | None) -> None:
        """Apply token usage statistics to session context state."""
        if not usage:
            return
        session = self._require_session()
        if not session.context_manager:
            return
        session.context_manager.set_latest_usage(usage)
        session.context_manager.add_usage(usage)

    def _build_context_stats(self, tools_schema: list[dict] | None) -> dict | None:
        """Build debug context-window stats, including tool schema token cost."""
        if not self._config.debug:
            return None
        session = self._require_session()
        if not session.context_manager:
            return None
        tool_schema_tokens = count_tokens(json.dumps(tools_schema), self._config.model) if tools_schema else 0
        return session.context_manager.get_context_stats(tool_schema_tokens)

    async def run(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """Run one user prompt through the agent loop and emit runtime events."""
        session = self._require_session()
        ctx = self._require_context()
        self._loop_detector.unsuppress()
        yield AgentEvent.from_start(prompt)

        hook_engine = session.hook_engine
        if hook_engine and hook_engine.has_hooks:
            submit_result = await hook_engine.fire(
                HookEvent.USER_PROMPT_SUBMIT,
                extra={"prompt": prompt},
            )
            if submit_result.should_block:
                reason = submit_result.blocking_reason or "Blocked by UserPromptSubmit hook"
                yield AgentEvent.from_error(f"Prompt blocked: {reason}")
                yield AgentEvent.from_end(None)
                return
            if submit_result.should_stop:
                yield AgentEvent.from_end(None)
                return
            extra_ctx = submit_result.collect_additional_context()
            if extra_ctx:
                ctx.get_user_message(f"[Hook context: {extra_ctx}]")

        ctx.get_user_message(prompt)
        session.save_message(role="user", content=prompt)

        final_response: str | None = None
        async for event in self._agentic_loop(prompt):
            yield event

            if event.type == AgentEventType.TEXT_COMPLETE:
                final_response = event.data.get("content")

        if hook_engine and hook_engine.has_hooks:
            stop_result = await hook_engine.fire(
                HookEvent.STOP,
                extra={"stop_hook_active": hook_engine.stop_hook_active},
            )
            if stop_result.should_block and not hook_engine.stop_hook_active:
                hook_engine.stop_hook_active = True
                reason = stop_result.blocking_reason or "Hook requires continued work"
                ctx.get_user_message(f"[Hook feedback: {reason}. Continue working.]")
                async for event in self._agentic_loop(reason):
                    yield event
                    if event.type == AgentEventType.TEXT_COMPLETE:
                        final_response = event.data.get("content")

        yield AgentEvent.from_end(final_response)

    async def _agentic_loop(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        """Execute iterative LLM/tool turns until the run terminates."""
        session = self._require_session()
        ctx = self._require_context()
        max_turns = self._config.limits.max_turns

        cp_mgr = session.checkpoint_manager
        if cp_mgr:
            next_turn = session.turn_count + 1
            msg_idx = ctx.message_count
            cp_mgr.begin_turn(next_turn, prompt[:200], msg_idx)

        for _ in range(max_turns):
            current_turn = session.increment_turn()

            if self._config.debug:
                logger.debug(f"=== Turn {current_turn}/{max_turns} ===")

            response_text = ""
            if ctx.needs_compression():
                hook_engine = session.hook_engine
                if hook_engine and hook_engine.has_hooks:
                    await hook_engine.fire(
                        HookEvent.PRE_COMPACT,
                        match_value="auto",
                        extra={"trigger": "auto", "custom_instructions": ""},
                    )

                (
                    summary,
                    compact_usage,
                    original_tokens,
                ) = await session.chat_compactor.compact_context(
                    ctx,
                    max_tokens=int(self._config.limits.context_window * 0.5),
                )
                if summary:
                    ctx.replace_with_summary(summary)
                    self._apply_usage(compact_usage)
                    yield AgentEvent.context_compacted(
                        original_tokens=original_tokens,
                        new_tokens=compact_usage.total_tokens if compact_usage else 0,
                        summary_tokens=compact_usage.completion_tokens if compact_usage else 0,
                    )

            pruned_count, tokens_saved = ctx.prune_tool_outputs_with_stats()
            if pruned_count > 0:
                yield AgentEvent.context_pruned(
                    pruned_count=pruned_count,
                    tokens_saved=tokens_saved,
                )

            tools_schema = session.tool_registry.get_schema()
            tool_calls: list[ToolCall] = []
            usage: TokenUsage | None = None
            got_any_event = False

            retry_events: list[AgentEvent] = []

            def _on_retry(
                attempt: int,
                max_attempts: int,
                error: str,
                wait_seconds: float,
            ) -> None:
                retry_events.append(AgentEvent.llm_retry(attempt, max_attempts, error, wait_seconds))

            session.client.on_retry(_on_retry)
            try:
                yield AgentEvent.thinking_start(current_turn)
                async for event in session.client.chat_completion(
                    messages=ctx.get_messages(),
                    tools=tools_schema if tools_schema else None,
                    stream=True,
                ):
                    for retry_evt in retry_events:
                        yield retry_evt
                    retry_events.clear()

                    if not got_any_event:
                        got_any_event = True
                        yield AgentEvent.thinking_end(current_turn)

                    if event.type == StreamEventType.TEXT_DELTA:
                        content = event.text_delta.content if event.text_delta else ""
                        response_text += content
                        yield AgentEvent.from_text_delta(content)
                    elif event.type == StreamEventType.TOOL_CALL_COMPLETE:
                        if event.tool_call:
                            tool_calls.append(event.tool_call)
                    elif event.type == StreamEventType.ERROR:
                        yield AgentEvent.from_error(event.error or "Unknown error occurred")
                    elif event.type == StreamEventType.MESSAGE_COMPLETE:
                        usage = event.usage

                for retry_evt in retry_events:
                    yield retry_evt
            finally:
                session.client.remove_retry_callback(_on_retry)

            if not got_any_event:
                yield AgentEvent.thinking_end(current_turn)

            ctx.get_agent_message(
                response_text or None,
                (
                    [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(tool_call.arguments),
                            },
                        }
                        for tool_call in tool_calls
                    ]
                    if tool_calls
                    else None
                ),
            )

            session.save_message(
                role="assistant",
                content=response_text or None,
                tool_calls=(
                    [
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
                    if tool_calls
                    else None
                ),
            )

            if response_text:
                yield AgentEvent.from_text_complete(response_text)

            if not tool_calls:
                self._apply_usage(usage)
                stats = self._build_context_stats(tools_schema)
                if stats:
                    yield AgentEvent.context_stats(stats)
                if not response_text:
                    yield AgentEvent.empty_response(current_turn)
                if self._config.debug:
                    logger.debug("No tool calls, exiting agentic loop")
                if cp_mgr:
                    cp_mgr.commit_turn()
                return

            if self._config.debug:
                for tc in tool_calls:
                    exists = session.tool_registry.get(tc.name) is not None
                    status = "✓" if exists else "✗ NOT REGISTERED"
                    logger.debug(f"Tool call: {tc.name} [{status}]")
                    logger.debug(f"  Arguments: {tc.arguments}")

            tool_call_results: list[ToolResultMessage] = []
            for tool_call in tool_calls:
                yield AgentEvent.tool_call_start(
                    tool_call.id,
                    tool_call.name,
                    tool_call.arguments,
                )

                result = await session.tool_registry.invoke(
                    tool_call.name,
                    tool_call.arguments,
                    self._config.cwd,
                    approval_manager=session.approval_manager,
                    hook_engine=session.hook_engine,
                )

                if result.metadata and result.metadata.get("reason"):
                    reason = result.metadata["reason"]
                    if reason in (
                        "denied by policy",
                        "dangerous command blocked",
                        "user_rejected",
                        "hook_denied",
                    ):
                        yield AgentEvent.approval_denied(tool_call.name, reason)

                yield AgentEvent.tool_call_complete(
                    tool_call.id,
                    tool_call.name,
                    result,
                )

                tool_call_results.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.id,
                        content=result.to_model_output(),
                        is_error=not result.success,
                    )
                )

            for tool_result in tool_call_results:
                ctx.add_tool_result(
                    tool_result.tool_call_id,
                    tool_result.content,
                )
                session.save_message(
                    role="tool",
                    content=tool_result.content,
                    tool_call_id=tool_result.tool_call_id,
                )

            self._apply_usage(usage)
            stats = self._build_context_stats(tools_schema)
            if stats:
                yield AgentEvent.context_stats(stats)

            errors_this_turn = [r.content for r in tool_call_results if r.is_error]
            turn_record = TurnRecord(
                turn_number=current_turn,
                tool_calls=[(tc.name, _hash_args(tc.arguments)) for tc in tool_calls],
                response_text=response_text,
                response_hash=_hash_text(response_text) if response_text else "",
                errors=errors_this_turn,
            )
            self._loop_detector.record_turn(turn_record)
            loop_result = self._loop_detector.check()
            if loop_result:
                yield AgentEvent.loop_detected(
                    strategy=loop_result.strategy.value,
                    description=loop_result.description,
                    turn_count=loop_result.turn_count,
                    action_taken=loop_result.action.value,
                )

                hook_engine = session.hook_engine
                if hook_engine and hook_engine.has_hooks:
                    await hook_engine.fire(
                        HookEvent.NOTIFICATION,
                        match_value="loop_detected",
                        extra={
                            "message": loop_result.description,
                            "title": "Loop Detected",
                            "notification_type": "loop_detected",
                        },
                    )

                if loop_result.action == LoopAction.STOP:
                    if cp_mgr:
                        cp_mgr.commit_turn()
                    return
                elif loop_result.action == LoopAction.WARN_AND_NUDGE:
                    ctx.get_user_message(loop_result.nudge_message)

    async def __aenter__(self) -> Agent:
        """Initialize async session resources and fire startup hooks."""
        session = self._require_session()
        await session.initialize()

        hook_engine = session.hook_engine
        if hook_engine and hook_engine.has_hooks:
            start_result = await hook_engine.fire(
                HookEvent.SESSION_START,
                match_value="startup",
                extra={
                    "source": "startup",
                    "model": self._config.model,
                },
            )
            extra_ctx = start_result.collect_additional_context()
            ctx = session.context_manager
            if extra_ctx and ctx is not None:
                ctx.get_user_message(f"[Session hook context: {extra_ctx}]")

        return self

    async def __aexit__(self, exc_type, exc_val, tb) -> None:
        """Fire shutdown hooks and release client/MCP resources."""
        session = self.session
        if not session:
            return

        hook_engine = session.hook_engine
        if hook_engine and hook_engine.has_hooks:
            await hook_engine.fire(
                HookEvent.SESSION_END,
                match_value="other",
                extra={"reason": "other"},
            )

        if session.client:
            await session.client.close()
        await session._mcp_manager.shutdown()
        self.session = None

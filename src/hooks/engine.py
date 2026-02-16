"""Hook execution engine for lifecycle automation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from hooks.types import (
    _MATCHER_EVENTS,
    HookEvent,
    HookFireResult,
    HookHandler,
    HookInput,
    HookMatcher,
    HookResult,
)
from utils.runtime_logging import configure_component_file_logger

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """A normalized view of one registered hook handler."""

    event: HookEvent
    matcher: str | None
    handler: HookHandler


def _setup_hooks_file_logger(cwd: str) -> None:
    """Route hooks logs to `<cwd>/logs/hooks/hooks.log`."""
    configure_component_file_logger(
        logger_name="hooks",
        path=os.path.join(cwd, "logs", "hooks", "hooks.log"),
        level="DEBUG",
    )


class HookEngine:
    """Core hook execution engine.

    Loads hook configuration once at startup (snapshot model — mid-session
    config changes are ignored for session safety and consistency).
    """

    def __init__(
        self,
        hooks_config: dict[str, list[HookMatcher]] | None = None,
        session_id: str = "",
        cwd: str = "",
        permission_mode: str = "on_request",
        disabled: bool = False,
        transcript_path: str = "",
    ) -> None:
        # Event → list of matcher groups
        self._hooks: dict[HookEvent, list[HookMatcher]] = {}
        self._session_id = session_id
        self._cwd = cwd
        self._permission_mode = permission_mode
        self._disabled = disabled
        self._transcript_path = transcript_path
        self._stop_hook_active = False  # prevents infinite Stop hook loops

        if hooks_config:
            self._load(hooks_config)

        # Route all hooks.* logging to logs/hooks/hooks.log
        if self._cwd:
            try:
                _setup_hooks_file_logger(self._cwd)
            except OSError as e:
                logger.debug("Skipping hooks file logger setup for cwd '%s': %s", self._cwd, e)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, hooks_config: dict[str, list[HookMatcher]]) -> None:
        """Load hooks from a pre-parsed config dict mapping event names to matchers."""
        for event_name, matchers in hooks_config.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning(f"Unknown hook event in config: {event_name!r}")
                continue

            if matchers:
                self._hooks[event] = matchers
                handler_count = sum(len(m.hooks) for m in matchers)
                logger.debug(f"Loaded {handler_count} hook handler(s) for {event.value}")

    @property
    def has_hooks(self) -> bool:
        """True if any hooks are registered."""
        return bool(self._hooks) and not self._disabled

    def get_registered_events(self) -> list[HookEvent]:
        """Return list of events that have hooks registered."""
        return list(self._hooks.keys())

    def get_hook_count(self, event: HookEvent | None = None) -> int:
        """Count total hook handlers, optionally for a specific event."""
        if event:
            matchers = self._hooks.get(event, [])
            return sum(len(m.hooks) for m in matchers)
        return sum(len(m.hooks) for matchers in self._hooks.values() for m in matchers)

    def get_registrations(
        self,
        event: HookEvent | None = None,
    ) -> list[HookRegistration]:
        """Return registered handlers as normalized rows for inspection/UIs."""
        registrations: list[HookRegistration] = []
        events = [event] if event else self.get_registered_events()
        for current_event in events:
            for matcher in self._hooks.get(current_event, []):
                for handler in matcher.hooks:
                    registrations.append(
                        HookRegistration(
                            event=current_event,
                            matcher=matcher.matcher,
                            handler=handler,
                        )
                    )
        return registrations

    # ------------------------------------------------------------------
    # Firing
    # ------------------------------------------------------------------

    async def fire(
        self,
        event: HookEvent,
        match_value: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> HookFireResult:
        """Fire all matching hooks for an event.

        Args:
            event: The lifecycle event that occurred.
            match_value: Value to match against (tool_name, source, etc.).
            extra: Event-specific fields to include in the JSON input.

        Returns:
            Aggregated result from all matching hooks.
        """
        fire_result = HookFireResult(event=event)

        if self._disabled or event not in self._hooks:
            return fire_result

        input_json = self._build_input_json(event, extra=extra)
        matching_handlers = self._resolve_handlers(event, match_value=match_value)

        if not matching_handlers:
            return fire_result

        logger.debug(
            f"Firing {len(matching_handlers)} hook(s) for {event.value}"
            + (f" (match: {match_value!r})" if match_value else "")
        )

        unique_handlers = self._deduplicate_handlers(matching_handlers)

        # Separate sync and async handlers
        sync_handlers = [h for h in unique_handlers if not h.async_]
        async_handlers = [h for h in unique_handlers if h.async_]

        # Run sync handlers in parallel
        if sync_handlers:
            tasks = [self._run_handler(handler, input_json, event) for handler in sync_handlers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(f"Hook handler error: {result}")
                    fire_result.results.append(HookResult(exit_code=1, stderr=str(result)))
                else:
                    fire_result.results.append(result)

        # Launch async (background) handlers — fire and forget
        for handler in async_handlers:
            asyncio.create_task(self._run_background_handler(handler, input_json, event))

        return fire_result

    def _build_input_json(
        self,
        event: HookEvent,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Build the JSON stdin payload sent to hook subprocesses."""
        hook_input = HookInput(
            session_id=self._session_id,
            transcript_path=self._transcript_path,
            cwd=self._cwd,
            hook_event_name=event.value,
            permission_mode=self._permission_mode,
            extra=extra or {},
        )
        input_data = hook_input.to_json_dict()
        if event in (HookEvent.STOP, HookEvent.SUBAGENT_STOP):
            input_data["stop_hook_active"] = self._stop_hook_active
        return json.dumps(input_data)

    def _resolve_handlers(
        self,
        event: HookEvent,
        match_value: str | None = None,
    ) -> list[HookHandler]:
        """Resolve handlers matching an event and optional matcher input."""
        handlers: list[HookHandler] = []
        for matcher in self._hooks.get(event, []):
            if self._matches(matcher.matcher, match_value, event):
                handlers.extend(matcher.hooks)
        return handlers

    def _deduplicate_handlers(self, handlers: list[HookHandler]) -> list[HookHandler]:
        """Deduplicate handlers while preserving the first-seen execution order."""
        seen: set[tuple[str, str, int, bool, str | None]] = set()
        deduped: list[HookHandler] = []
        for handler in handlers:
            key = (
                handler.type,
                handler.command,
                handler.timeout,
                handler.async_,
                handler.status_message,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(handler)
        return deduped

    # ------------------------------------------------------------------
    # Handler execution
    # ------------------------------------------------------------------

    async def _run_handler(self, handler: HookHandler, input_json: str, event: HookEvent) -> HookResult:
        """Execute a single command hook handler."""
        if handler.type != "command":
            logger.warning(f"Unsupported hook type: {handler.type!r}")
            return HookResult(exit_code=1, stderr=f"Unsupported hook type: {handler.type}")

        command = self._expand_env_vars(handler.command)
        timeout = handler.timeout or 60

        logger.debug(f"Running hook: {command} (timeout: {timeout}s)")
        proc: asyncio.subprocess.Process | None = None

        try:
            # Use subprocess_shell on all platforms for consistent behavior
            # On Windows, cmd.exe handles the command; on Unix, /bin/sh
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd or None,
                env=self._get_hook_env(),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_json.encode("utf-8")),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            exit_code = proc.returncode or 0

            logger.debug(f"Hook completed: exit={exit_code}, stdout={len(stdout)} bytes, stderr={len(stderr)} bytes")

            result = HookResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

            # Parse JSON stdout on exit 0
            if exit_code == 0 and stdout:
                self._parse_json_output(result, stdout)

            return result

        except asyncio.TimeoutError:
            logger.warning(f"Hook timed out after {timeout}s: {command}")
            # Try to kill the process
            if proc is not None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.communicate(), timeout=2)
                except Exception as kill_error:
                    logger.debug("Failed to kill timed-out hook process: %s", kill_error)
            return HookResult(
                exit_code=1,
                stderr=f"Hook timed out after {timeout}s",
                timed_out=True,
            )
        except Exception as e:
            logger.error(f"Hook execution failed: {e}")
            return HookResult(exit_code=1, stderr=str(e))
        finally:
            if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                    await proc.stdin.wait_closed()
                except Exception as close_error:
                    logger.debug("Failed closing hook stdin pipe: %s", close_error)
            if proc is not None:
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    try:
                        transport.close()
                    except Exception as close_error:
                        logger.debug("Failed closing hook process transport: %s", close_error)

    async def _run_background_handler(self, handler: HookHandler, input_json: str, event: HookEvent) -> None:
        """Run a hook handler in the background (async=true). Fire and forget."""
        try:
            result = await self._run_handler(handler, input_json, event)
            if result.system_message:
                # Background hooks can deliver system messages on next turn
                logger.info(f"Background hook message: {result.system_message}")
            if result.additional_context:
                logger.info(f"Background hook context: {result.additional_context}")
        except Exception as e:
            logger.error(f"Background hook error: {e}")

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _matches(
        self,
        pattern: str | None,
        value: str | None,
        event: HookEvent,
    ) -> bool:
        """Check if a matcher pattern matches the value.

        - If pattern is None, empty, or "*", match everything.
        - If the event doesn't support matchers, always match.
        - Otherwise, use regex matching.
        """
        # Events that don't support matchers always fire
        if event not in _MATCHER_EVENTS:
            return True

        # No pattern = match all
        if not pattern or pattern == "*":
            return True

        # No value provided but pattern exists → no match
        if not value:
            return False

        try:
            return bool(re.search(pattern, value))
        except re.error as e:
            logger.warning(f"Invalid hook matcher regex {pattern!r}: {e}")
            return False

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _parse_json_output(self, result: HookResult, stdout: str) -> None:
        """Parse JSON stdout from a hook and populate result fields."""
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Not JSON — could be plain text (valid for some events)
            logger.debug("Hook stdout is not JSON, treating as plain text")
            return

        if not isinstance(data, dict):
            return

        # Universal fields
        result.continue_ = data.get("continue", True)
        result.stop_reason = data.get("stopReason")
        result.suppress_output = data.get("suppressOutput", False)
        result.system_message = data.get("systemMessage")
        result.additional_context = data.get("additionalContext")

        # Top-level decision (UserPromptSubmit, PostToolUse, Stop, etc.)
        result.decision = data.get("decision")
        result.reason = data.get("reason")

        # hookSpecificOutput (PreToolUse, PermissionRequest, etc.)
        hso = data.get("hookSpecificOutput")
        if isinstance(hso, dict):
            result.hook_specific_output = hso
            # Also pull additionalContext from hookSpecificOutput
            if not result.additional_context:
                result.additional_context = hso.get("additionalContext")

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    def _get_hook_env(self) -> dict[str, str]:
        """Build environment variables for hook subprocess."""
        env = os.environ.copy()
        env["PICHU_PROJECT_DIR"] = self._cwd
        env["PICHU_SESSION_ID"] = self._session_id
        env["PICHU_HOOK"] = "1"

        # Legacy compatibility alias for older integrations.
        env["PICHU_PROJECT_DIR"] = self._cwd

        return env

    @property
    def stop_hook_active(self) -> bool:
        """Whether a Stop hook has already forced continuation."""
        return self._stop_hook_active

    @stop_hook_active.setter
    def stop_hook_active(self, value: bool) -> None:
        self._stop_hook_active = value

    @property
    def transcript_path(self) -> str:
        return self._transcript_path

    @transcript_path.setter
    def transcript_path(self, value: str) -> None:
        self._transcript_path = value

    def _expand_env_vars(self, command: str) -> str:
        """Expand known environment variables in the command string."""
        replacements = {
            "$PICHU_PROJECT_DIR": self._cwd,
            "${PICHU_PROJECT_DIR}": self._cwd,
        }
        for var, val in replacements.items():
            command = command.replace(var, val)
        return command

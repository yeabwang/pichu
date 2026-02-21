from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, RenderableType
from rich.live import Live
from rich.rule import Rule
from rich.text import Text

from ui.runtime.task_runner import RuntimeEventType, RuntimeStatusEvent


@dataclass(slots=True)
class TaskFooterState:
    in_progress: int = 0
    ready: int = 0
    blocked: int = 0
    done: int = 0

    def update_from_stats(self, stats: dict[str, Any]) -> bool:
        next_in_progress = int(stats.get("in_progress", self.in_progress))
        next_ready = int(stats.get("available", self.ready))
        next_blocked = int(stats.get("blocked", self.blocked))
        next_done = int(stats.get("completed", self.done))
        changed = (
            next_in_progress != self.in_progress
            or next_ready != self.ready
            or next_blocked != self.blocked
            or next_done != self.done
        )
        self.in_progress = next_in_progress
        self.ready = next_ready
        self.blocked = next_blocked
        self.done = next_done
        return changed

    def to_text(self) -> str:
        return f"Tasks: in-progress {self.in_progress} • ready {self.ready} • blocked {self.blocked} • done {self.done}"


@dataclass(slots=True)
class RuntimeProgressState:
    active: int = 0
    queued: int = 0
    progress_current: int | None = None
    progress_total: int | None = None
    last_message: str = ""

    def update_from_event(self, event: RuntimeStatusEvent) -> bool:
        before = (self.active, self.queued, self.progress_current, self.progress_total, self.last_message)

        if event.event_type == RuntimeEventType.QUEUED:
            self.queued += 1
        elif event.event_type == RuntimeEventType.STARTED:
            if self.queued > 0:
                self.queued -= 1
            self.active += 1
            if event.message:
                self.last_message = event.message
        elif event.event_type == RuntimeEventType.PROGRESS:
            self.progress_current = event.progress_current
            self.progress_total = event.progress_total
            if event.message:
                self.last_message = event.message
        elif event.event_type in (RuntimeEventType.COMPLETED, RuntimeEventType.FAILED, RuntimeEventType.CANCELLED):
            if self.active > 0:
                self.active -= 1
            if self.active == 0 and self.queued == 0:
                self.progress_current = None
                self.progress_total = None

        after = (self.active, self.queued, self.progress_current, self.progress_total, self.last_message)
        return before != after

    def to_text(self) -> str:
        segments: list[str] = [f"active {self.active}"]
        if self.queued:
            segments.append(f"queued {self.queued}")
        if self.progress_total and self.progress_current is not None:
            segments.append(f"{self.progress_current}/{self.progress_total}")
        if self.last_message:
            message = self.last_message.strip()
            if len(message) > 42:
                message = message[:39] + "..."
            segments.append(message)
        return " • ".join(segments)


@dataclass(slots=True)
class RenderState:
    stream_buffer: str = ""
    task_footer: TaskFooterState = field(default_factory=TaskFooterState)
    runtime_progress: RuntimeProgressState = field(default_factory=RuntimeProgressState)
    block_spacing_lines: int = 1
    has_output_blocks: bool = False


class DifferentialRichRenderer:
    def __init__(self, console: Console, state: RenderState) -> None:
        self.console = console
        self.state = state
        self._stream_flush_threshold = 256
        self._footer_debounce_seconds = 0.075
        self._footer_refresh_pending = False
        self._footer_refresh_deadline = 0.0
        self._footer_live: Live | None = None
        self._footer_enabled = bool(getattr(self.console, "is_terminal", False))
        self._footer_suspended = False
        self._agent_stream_active = False
        self._agent_stream_had_output = False
        self._last_stream_chunk_ended_with_newline = True

    def _footer_text(self) -> str:
        base = self.state.task_footer.to_text()
        if self.state.runtime_progress.active > 0 or self.state.runtime_progress.queued > 0:
            return f"{base} • {self.state.runtime_progress.to_text()}"
        return base

    def footer_text(self) -> str:
        return self._footer_text()

    def set_footer_live_enabled(self, enabled: bool) -> None:
        if self._footer_enabled == enabled:
            return
        self._footer_enabled = enabled
        if not enabled and self._footer_live:
            self._footer_live.stop()
            self._footer_live = None

    def _ensure_footer_live(self) -> Live | None:
        if not self._footer_enabled:
            return None
        if self._footer_suspended:
            return None
        if self._footer_live is None:
            footer = Text(self._footer_text(), style="footer")
            self._footer_live = Live(
                footer,
                console=self.console,
                refresh_per_second=10,
                transient=True,
                auto_refresh=False,
            )
            self._footer_live.start()
            self._footer_live.refresh()
        return self._footer_live

    def _suspend_footer_live(self) -> None:
        self._footer_suspended = True
        if self._footer_live:
            self._footer_live.stop()
            self._footer_live = None

    def _resume_footer_live(self) -> None:
        if not self._footer_enabled:
            self._footer_suspended = False
            return
        if not self._footer_suspended:
            return
        self._footer_suspended = False
        self._ensure_footer_live()

    def _start_output_block(self) -> None:
        self.flush_stream_buffer()
        if self.state.has_output_blocks:
            for _ in range(max(self.state.block_spacing_lines, 0)):
                self.console.print()
        self.state.has_output_blocks = True

    def _is_blank_renderable(self, renderable: RenderableType) -> bool:
        if isinstance(renderable, str):
            return renderable.strip() == ""
        if isinstance(renderable, Text):
            return renderable.plain.strip() == ""
        return False

    def _trim_outer_blank_renderables(self, renderables: list[RenderableType]) -> list[RenderableType]:
        start = 0
        end = len(renderables)
        while start < end and self._is_blank_renderable(renderables[start]):
            start += 1
        while end > start and self._is_blank_renderable(renderables[end - 1]):
            end -= 1
        return renderables[start:end]

    def begin_prompt_block(self) -> None:
        self._start_output_block()

    def begin_agent_response(self) -> None:
        self.flush_deferred_updates(force=True)
        self._suspend_footer_live()
        self._agent_stream_active = True
        self._agent_stream_had_output = False
        self._last_stream_chunk_ended_with_newline = True
        self._start_output_block()
        self.console.print(Rule(Text("Agent", style="agent")))

    def stream_agent_delta(self, content: str) -> None:
        self.state.stream_buffer += content
        if "\n" in content or len(self.state.stream_buffer) >= self._stream_flush_threshold:
            self.flush_stream_buffer()

    def end_agent_response(self) -> None:
        self.flush_stream_buffer()
        if self._agent_stream_had_output and not self._last_stream_chunk_ended_with_newline:
            self.console.print()
        self._agent_stream_active = False
        self._resume_footer_live()
        self.flush_deferred_updates(force=True)

    def flush_stream_buffer(self) -> None:
        if not self.state.stream_buffer:
            return
        buffer = self.state.stream_buffer
        self.console.print(buffer, style="streaming", end="", highlight=False)
        self.state.stream_buffer = ""
        self._agent_stream_had_output = True
        self._last_stream_chunk_ended_with_newline = buffer.endswith("\n")

    def print_renderable(self, renderable: RenderableType, *, with_leading_blank: bool = False) -> None:
        if with_leading_blank:
            self.flush_stream_buffer()
            self.console.print()
            self.console.print(renderable)
            self.state.has_output_blocks = True
            return
        self._start_output_block()
        self.console.print(renderable)

    def render_command_payload(self, renderables: list[RenderableType]) -> None:
        trimmed = self._trim_outer_blank_renderables(renderables)
        if not trimmed:
            return
        self._start_output_block()
        for renderable in trimmed:
            self.console.print(renderable)

    def update_task_footer(self, stats: dict[str, Any]) -> None:
        changed = self.state.task_footer.update_from_stats(stats)
        if not changed:
            return
        if not self._footer_enabled:
            return
        self._footer_refresh_pending = True
        self._footer_refresh_deadline = time.monotonic() + self._footer_debounce_seconds

    def flush_deferred_updates(self, *, force: bool = False) -> None:
        if not self._footer_refresh_pending:
            return
        footer_live = self._ensure_footer_live()
        if footer_live is None:
            return
        if not force and self._agent_stream_active:
            return
        if not force and self.state.stream_buffer:
            return
        if not force and time.monotonic() < self._footer_refresh_deadline:
            return
        footer_live.update(Text(self._footer_text(), style="footer"), refresh=True)
        self._footer_refresh_pending = False

    def update_runtime_status(self, event: RuntimeStatusEvent) -> None:
        changed = self.state.runtime_progress.update_from_event(event)
        if not changed:
            return
        if not self._footer_enabled:
            return
        self._footer_refresh_pending = True
        self._footer_refresh_deadline = time.monotonic()

    def close(self) -> None:
        self.flush_deferred_updates(force=True)
        if self._footer_live:
            self._footer_live.stop()
            self._footer_live = None

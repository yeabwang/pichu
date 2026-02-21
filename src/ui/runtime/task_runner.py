from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from commands.base import CommandDisplayPayload, CommandResult


class RuntimeTaskCancelledError(RuntimeError):
    """Raised when a runtime task was cancelled."""


class RuntimeEventType(str, Enum):
    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class RuntimeTaskResult:
    command_result: CommandResult | None = None
    display_payload: CommandDisplayPayload | None = None
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeStatusEvent:
    task_id: str
    operation: str
    event_type: RuntimeEventType
    message: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    error: str | None = None
    result: RuntimeTaskResult | None = None


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False
        self._reason: str | None = None
        self._cancel_event = asyncio.Event()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str | None = None) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._reason = reason
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancelled

    async def wait_cancelled(self) -> None:
        await self._cancel_event.wait()

    def raise_if_cancelled(self) -> None:
        if not self._cancelled:
            return
        raise RuntimeTaskCancelledError(self._reason or "Cancelled")


@dataclass(slots=True)
class RuntimeTaskContext:
    task_id: str
    operation: str
    token: CancellationToken
    _emit: Callable[[RuntimeStatusEvent], None]

    def emit(
        self,
        event_type: RuntimeEventType,
        *,
        message: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        error: str | None = None,
        result: RuntimeTaskResult | None = None,
    ) -> None:
        self._emit(
            RuntimeStatusEvent(
                task_id=self.task_id,
                operation=self.operation,
                event_type=event_type,
                message=message,
                progress_current=progress_current,
                progress_total=progress_total,
                error=error,
                result=result,
            )
        )

    def emit_progress(self, current: int, total: int, message: str | None = None) -> None:
        self.emit(
            RuntimeEventType.PROGRESS,
            message=message,
            progress_current=current,
            progress_total=total,
        )

    def raise_if_cancelled(self) -> None:
        self.token.raise_if_cancelled()


class TaskHandle:
    def __init__(
        self,
        task_id: str,
        operation: str,
        token: CancellationToken,
        task: asyncio.Task[RuntimeTaskResult],
    ) -> None:
        self.task_id = task_id
        self.operation = operation
        self._token = token
        self._task = task

    def cancel(self, reason: str | None = None) -> None:
        self._token.cancel(reason)
        if not self._task.done():
            self._task.cancel()

    def done(self) -> bool:
        return self._task.done()

    async def result(self) -> RuntimeTaskResult:
        return await self._task

    @property
    def token(self) -> CancellationToken:
        return self._token


class RuntimeTaskRunner:
    def __init__(self) -> None:
        self._task_seq = itertools.count(1)
        self._events: asyncio.Queue[RuntimeStatusEvent] = asyncio.Queue()

    def submit(
        self,
        operation: str,
        run: Callable[[RuntimeTaskContext], Awaitable[RuntimeTaskResult]],
    ) -> TaskHandle:
        task_id = f"rt-{next(self._task_seq)}"
        token = CancellationToken()

        def _emit(event: RuntimeStatusEvent) -> None:
            self._events.put_nowait(event)

        context = RuntimeTaskContext(task_id=task_id, operation=operation, token=token, _emit=_emit)
        _emit(RuntimeStatusEvent(task_id=task_id, operation=operation, event_type=RuntimeEventType.QUEUED))

        async def _runner() -> RuntimeTaskResult:
            context.emit(RuntimeEventType.STARTED)
            try:
                result = await run(context)
                token.raise_if_cancelled()
                context.emit(
                    RuntimeEventType.COMPLETED,
                    progress_current=1,
                    progress_total=1,
                    result=result,
                )
                return result
            except RuntimeTaskCancelledError as exc:
                context.emit(RuntimeEventType.CANCELLED, error=str(exc))
                raise
            except asyncio.CancelledError as exc:
                reason = token.reason or str(exc) or "Cancelled"
                context.emit(RuntimeEventType.CANCELLED, error=reason)
                raise RuntimeTaskCancelledError(reason) from exc
            except Exception as exc:
                context.emit(RuntimeEventType.FAILED, error=str(exc))
                raise

        task = asyncio.create_task(_runner(), name=task_id)
        return TaskHandle(task_id=task_id, operation=operation, token=token, task=task)

    def drain_events(self, max_items: int | None = None) -> list[RuntimeStatusEvent]:
        items: list[RuntimeStatusEvent] = []
        while True:
            if max_items is not None and len(items) >= max_items:
                break
            try:
                items.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

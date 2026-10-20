"""Background task support for agent tool calls.

Provides:
- _bg_output_buffer: contextvars.ContextVar for per-task output capture
- _install_print_hook: monkeypatch builtins.print for reliable stdout capture
- BackgroundTask: dataclass tracking a background tool execution
- BackgroundTaskManager: manages lifecycle of background tasks
"""

import asyncio
import builtins
import contextvars
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Per-asyncio-task output buffer (None = not capturing)
_bg_output_buffer: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "_bg_output_buffer", default=None
)

# ---------------------------------------------------------------------------
# Reliable print() capture via builtins.print monkeypatch
# ---------------------------------------------------------------------------
_original_print = builtins.print
_print_hook_installed = False


def _bg_aware_print(*args, **kwargs):
    """Print replacement that tee's output to the background task buffer."""
    buf = _bg_output_buffer.get()
    # Only capture stdout-directed prints (no explicit file= argument)
    if buf is not None and "file" not in kwargs:
        sio = io.StringIO()
        _original_print(*args, file=sio, **kwargs)
        text = sio.getvalue().rstrip("\n")
        if text:
            buf.append(text)
    _original_print(*args, **kwargs)


def _install_print_hook() -> None:
    """Install the print monkeypatch (idempotent)."""
    global _print_hook_installed
    if _print_hook_installed:
        return
    builtins.print = _bg_aware_print
    _print_hook_installed = True


def _bg_report(message: str) -> None:
    """Append a progress line to the background task output buffer, if active.

    This is a no-op when not running inside a background task context.
    Use this in tool implementations and agent internals to provide
    incremental progress that background_task() can return.
    """
    buf = _bg_output_buffer.get()
    if buf is not None:
        buf.append(message)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackgroundTask:
    task_id: str  # "bg_1", "bg_2", ...
    tool_name: str
    tool_call_id: str  # original LLM tool_call_id
    args: dict
    status: str = "running"  # running | completed | failed | cancelled
    asyncio_task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    result: Any = None
    error: str | None = None
    output_lines: list[str] = field(default_factory=list)
    source: str = "explicit"  # "explicit" | "timeout"


class BackgroundTaskManager:
    """Manages background task lifecycle: creation, adoption, status, cleanup."""

    def __init__(self, max_retained: int = 50):
        self._tasks: dict[str, BackgroundTask] = {}
        self._counter: int = 0
        self._max_retained = max_retained
        self._adopted_tasks: set[int] = set()  # id() of adopted asyncio.Tasks
        self._completed_notifications: list[BackgroundTask] = []
        # Callback fired when any task completes/fails/cancels.
        # Signature: on_complete(bg_task: BackgroundTask) -> None
        # Consumers (REPL, API, SDK) set this to react to completions.
        self.on_complete: Callable[[BackgroundTask], None] | None = None
        # Ensure print hook is active
        _install_print_hook()

    def _next_id(self) -> str:
        self._counter += 1
        return f"bg_{self._counter}"

    def start(
        self,
        tool_name: str,
        tool_call_id: str,
        args: dict,
        coro,
        source: str = "explicit",
    ) -> BackgroundTask:
        """Create a background task from a coroutine.

        Wraps the coroutine to set _bg_output_buffer contextvar for stdout capture.
        Registers done_callback for automatic status update.
        """
        task_id = self._next_id()
        bg_task = BackgroundTask(
            task_id=task_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args,
            source=source,
        )
        self._tasks[task_id] = bg_task

        async def _wrapped():
            token = _bg_output_buffer.set(bg_task.output_lines)
            try:
                return await coro
            finally:
                _bg_output_buffer.reset(token)

        bg_task.asyncio_task = asyncio.create_task(_wrapped())
        bg_task.asyncio_task.add_done_callback(
            lambda t: self._on_task_done(task_id, t)
        )
        self._evict_old()
        return bg_task

    def adopt(
        self,
        tool_name: str,
        tool_call_id: str,
        args: dict,
        existing_task: asyncio.Task,
        output_buffer: list[str] | None = None,
    ) -> BackgroundTask:
        """Adopt an already-running asyncio.Task (timeout scenario).

        Transfers pre-existing output_buffer. Registers done_callback.
        IMPORTANT: output_buffer should be the SAME list object that the
        contextvar points to, so post-adoption prints continue to accumulate.
        """
        task_id = self._next_id()
        bg_task = BackgroundTask(
            task_id=task_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args,
            source="timeout",
        )
        if output_buffer is not None:
            bg_task.output_lines = output_buffer
        bg_task.asyncio_task = existing_task
        self._adopted_tasks.add(id(existing_task))
        existing_task.add_done_callback(
            lambda t: self._on_task_done(task_id, t)
        )
        self._tasks[task_id] = bg_task
        self._evict_old()
        return bg_task

    def _on_task_done(self, task_id: str, asyncio_task: asyncio.Task) -> None:
        """Callback: set status, capture result/error, set completed_at."""
        bg_task = self._tasks.get(task_id)
        if bg_task is None:
            return

        bg_task.completed_at = time.time()

        if asyncio_task.cancelled():
            bg_task.status = "cancelled"
        else:
            exc = asyncio_task.exception()
            if exc is not None:
                bg_task.status = "failed"
                bg_task.error = repr(exc)
            else:
                bg_task.status = "completed"
                bg_task.result = asyncio_task.result()

        # Queue notification for agent auto-reporting
        self._completed_notifications.append(bg_task)

        # Fire external callback
        if self.on_complete is not None:
            try:
                self.on_complete(bg_task)
            except Exception as e:
                logger.warning(f"on_complete callback error: {e}")

        self._evict_old()

    def drain_notifications(self) -> list[BackgroundTask]:
        """Return and clear all pending completion notifications."""
        notifs = self._completed_notifications
        self._completed_notifications = []
        return notifs

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        bg_task = self._tasks.get(task_id)
        if bg_task is None:
            return False
        if bg_task.asyncio_task and not bg_task.asyncio_task.done():
            bg_task.asyncio_task.cancel()
            return True
        return False

    def remove(self, task_id: str) -> bool:
        """Remove a task from the manager. Cancels it first if still running."""
        bg_task = self._tasks.get(task_id)
        if bg_task is None:
            return False
        if bg_task.asyncio_task and not bg_task.asyncio_task.done():
            bg_task.asyncio_task.cancel()
        del self._tasks[task_id]
        return True

    def _is_adopted(self, asyncio_task: asyncio.Task) -> bool:
        """Check if an asyncio.Task has been adopted (used by finally guard)."""
        return id(asyncio_task) in self._adopted_tasks

    def to_summary(self, task: BackgroundTask) -> dict:
        """JSON-serializable summary for LLM consumption."""
        elapsed = (task.completed_at or time.time()) - task.created_at

        # Truncate result for LLM context
        result_str = None
        if task.result is not None:
            result_str = str(task.result)
            if len(result_str) > 2000:
                result_str = result_str[:2000] + "... (truncated)"

        return {
            "task_id": task.task_id,
            "tool_name": task.tool_name,
            "status": task.status,
            "source": task.source,
            "created_at": task.created_at,
            "elapsed_seconds": round(elapsed, 2),
            "result": result_str,
            "error": task.error,
            "recent_output": task.output_lines[-50:],
        }

    def _evict_old(self) -> None:
        """If len(_tasks) > _max_retained, remove oldest completed tasks."""
        if len(self._tasks) <= self._max_retained:
            return

        completed = [
            t for t in self._tasks.values()
            if t.status in ("completed", "failed", "cancelled")
        ]
        completed.sort(key=lambda t: t.created_at)

        while len(self._tasks) > self._max_retained and completed:
            old = completed.pop(0)
            if old.asyncio_task:
                self._adopted_tasks.discard(id(old.asyncio_task))
            del self._tasks[old.task_id]

    async def cleanup(self) -> None:
        """Cancel all running tasks. Called on agent shutdown."""
        for bg_task in list(self._tasks.values()):
            if bg_task.asyncio_task and not bg_task.asyncio_task.done():
                bg_task.asyncio_task.cancel()

        running = [
            bg_task.asyncio_task
            for bg_task in self._tasks.values()
            if bg_task.asyncio_task and not bg_task.asyncio_task.done()
        ]
        if running:
            await asyncio.gather(*running, return_exceptions=True)

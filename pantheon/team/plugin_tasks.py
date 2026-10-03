"""Background plugin work belongs to the composition that started it."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from pantheon.team.plugin import TeamPlugin
from pantheon.utils.log import logger


class BackgroundTaskPlugin(TeamPlugin):
    """Drain accepted post-run work before the App's providers are disposed.

    Extraction remains best-effort, as in the plugin hooks. Stopping rejects new
    background work but does not cancel an accepted write or LLM request. The
    App supervisor owns the hard-stop deadline, not this helper.
    """

    def __init__(self):
        self._background_tasks: set[asyncio.Task] = set()
        self._stopping = False
        self._drain_task: asyncio.Task | None = None

    def _start_background(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._stopping:
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def finished(done):
            self._background_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("{} background task failed: {}", type(self).__name__, done.exception())

        task.add_done_callback(finished)

    async def on_shutdown(self) -> None:
        self._stopping = True
        if self._drain_task is None:
            async def drain():
                await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

            self._drain_task = asyncio.create_task(drain())
        # Cancelling an observer does not terminate accepted plugin work.
        await asyncio.shield(self._drain_task)

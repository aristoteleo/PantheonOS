"""Local execution ownership while Evolution's tools move to App dependencies.

The caller observes a tool invocation; cancelling that observer does not prove
that a disk operation or kernel stopped. This provider retains the accepted
invocation until its owner drains it or explicitly cancels an async executor.
"""
import asyncio

from pantheon.providers import LocalProvider
from .lifetime import EvolutionCleanupError, join_cleanup


class EvolutionLocalProvider(LocalProvider):
    def __init__(self, toolset, *, cancel_calls=False, reset_after_iteration=False):
        super().__init__(toolset)
        self._cancel_calls = cancel_calls
        self._reset_after_iteration = reset_after_iteration
        self._calls = set()
        self._closing = None

    async def call_tool(self, name, args):
        if self._closing is not None:
            raise RuntimeError('Evolution tools are closed')
        task = asyncio.create_task(super().call_tool(name, args))
        self._calls.add(task)
        # Keep completed calls until the owner settles them, including failures.
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def settle(self):
        tasks = tuple(self._calls)
        if self._cancel_calls:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, EvolutionCleanupError)]
        if errors:
            raise EvolutionCleanupError(errors) from errors[0]
        self._calls.difference_update(tasks)
        if self._reset_after_iteration:
            await self.toolset.cleanup()

    async def shutdown(self):
        if self._closing is None:
            async def finish():
                errors = []
                try:
                    await self.settle()
                except BaseException as exc:
                    errors.append(exc)
                try:
                    await self.toolset.cleanup()
                except BaseException as exc:
                    errors.append(exc)
                if errors:
                    raise EvolutionCleanupError(errors) from errors[0]
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)

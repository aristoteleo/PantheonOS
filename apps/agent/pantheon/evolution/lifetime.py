"""Execution-owned resources used by Evolution's local workers."""
import asyncio
import os
import signal


class EvolutionCleanupError(RuntimeError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__('Evolution resources did not finish shutdown')


async def join_cleanup(task):
    """Do not release an execution owner while its teardown is still running."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    # Cleanup errors take precedence: a failed stop must not look cancelled/clean.
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def reap_process(spawn, communication):
    """Reap a shielded spawn and its owned POSIX process group, including pipes."""
    try:
        process = await spawn
    except Exception:
        return  # Setup failed without publishing a process handle.
    try:
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        if communication is not None:
            await communication
        else:
            await process.communicate()
        await process.wait()
    except Exception as exc:
        raise EvolutionCleanupError([exc]) from exc


async def finish_agent_tools(agent, *, cancel):
    """Settle accepted tool work before a worker reuses/releases its workspace."""
    manager = agent._bg_manager
    while tasks := [item.asyncio_task for item in manager.list_tasks()
                    if item.asyncio_task is not None and not item.asyncio_task.done()]:
        if cancel:
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, EvolutionCleanupError)]
        if errors:
            raise EvolutionCleanupError(errors) from errors[0]


class EvolutionResources:
    """Only resources explicitly created by this worker belong to this owner.

    Injected agents/evaluators and the shared archive are borrowed. A failed
    shutdown retains the owner and error instead of silently admitting a rerun.
    """
    def __init__(self):
        self._callbacks = []
        self._early = []
        self._closing = None

    def own(self, cleanup, *, early=False):
        if self._closing is not None:
            raise RuntimeError('Evolution resources are closing')
        (self._early if early else self._callbacks).append(cleanup)

    async def close(self):
        if self._closing is None:
            async def finish():
                errors = []
                # Plugins/clients go first, followed by their underlying tools.
                for callback in [*reversed(self._early), *reversed(self._callbacks)]:
                    try:
                        await callback()
                    except BaseException as exc:
                        errors.append(exc)
                if errors:
                    raise EvolutionCleanupError(errors) from errors[0]
                self._callbacks.clear()
                self._early.clear()
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)

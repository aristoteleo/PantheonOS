import asyncio
import functools
import os
import sys
from contextlib import contextmanager
from inspect import iscoroutinefunction

import loky.process_executor
if hasattr(loky.process_executor, "_MAX_MEMORY_LEAK_SIZE"):
    loky.process_executor._MAX_MEMORY_LEAK_SIZE = int(1e13)
from loky.process_executor import ProcessPoolExecutor

from .base import Job
from .utils import (
    _gen_initializer, create_generator_wrapper, run_async_func
)


@contextmanager
def _analysis_interpreter():
    """Spawn workers from the analysis env rather than the agent's runtime.

    The runtime venv is what keeps the agent alive; user analysis should not be
    installing into it. PANTHEON_ANALYSIS_PYTHON names a conda env on the
    persistent Volume to run the work in instead, so a package upgrade lands
    somewhere it can only break the analysis, and two projects with
    incompatible pins can have an env each.

    It has to be done by moving `sys.executable`, because on POSIX loky builds
    the child command line from it directly (`cmd_python = [sys.executable]` in
    backend/popen_loky_posix.py) and consults neither its own
    `spawn.get_executable()` nor any environment variable — patching that, the
    documented-looking seam, changes nothing on Linux.

    Held across pool creation only — see _spawn_executor for what "creation"
    has to include — so the agent's own idea of its interpreter is restored
    before any of its code runs with it. The env must be bridged back to the
    runtime's site-packages (pantheon-analysis-env writes that .pth): loky
    unpickles the job function in the child, so an interpreter that cannot
    import `pantheon` cannot start.
    """
    exe = os.environ.get("PANTHEON_ANALYSIS_PYTHON")
    # Unset is the ordinary case, and a stale path after an image change must
    # not take every interpreter down with it — fall through to the runtime.
    if not exe or not os.path.isfile(exe):
        yield False
        return
    original = sys.executable
    sys.executable = exe
    try:
        yield True
    finally:
        sys.executable = original


def _spawn_executor(*args, **kwargs):
    """Build a worker pool, in the analysis env when one is configured.

    loky starts its workers LAZILY — the constructor allocates nothing, and the
    first process is forked only when work arrives. Holding the interpreter
    swap across the constructor alone therefore did nothing at all: by the time
    a worker actually spawned, sys.executable had been put back, and the child
    came up in the runtime venv. Measured both ways in a sandbox — patch across
    the constructor gave `sys.prefix == /venv`, forcing the workers up inside
    the same window gave the analysis env.

    `_ensure_executor_running` is loky's private name for "start them now", so
    it is called defensively: if a future version drops it, this degrades to
    interpreters running in the runtime, which is what happens today anyway.
    """
    with _analysis_interpreter() as swapped:
        executor = ProcessPoolExecutor(*args, **kwargs)
        if swapped:
            ensure = getattr(executor, "_ensure_executor_running", None)
            if ensure is not None:
                try:
                    ensure()
                except Exception:  # noqa: BLE001 - never fail a job over this
                    pass
    return executor


class ProcessJob(Job):
    """Job that runs in a process."""""

    def has_resource(self) -> bool:
        """Check if the job has enough resource to run."""
        if self.engine is None:
            return False
        else:
            return (
                super().has_resource() and
                (self.engine.resource.n_process > 0)
            )

    def consume_resource(self) -> bool:
        """Consume resource for the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_process -= 1
            return (
                super().consume_resource() and
                True
            )

    def release_resource(self) -> bool:
        """Release resource for the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_process += 1
            return (
                super().release_resource() and
                True
            )

    async def run_function(self):
        """Run job in process pool."""
        func = functools.partial(self.func, *self.args, **self.kwargs)
        if iscoroutinefunction(func):
            func = functools.partial(run_async_func, func)
        self._executor = _spawn_executor(1)
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, func)
        result = await fut
        return result

    async def run_generator(self):
        """Run job as a generator."""
        func = functools.partial(self.func, *self.args, **self.kwargs)
        self._executor = _spawn_executor(
            1, initializer=_gen_initializer, initargs=(func,))
        result = create_generator_wrapper(self)
        return result

    async def cancel(self):
        """Cancel job."""
        if self.status == "running":
            self._executor.shutdown(wait=True, kill_workers=True)
        await super().cancel()

    def clear_context(self):
        """Clear context."""
        self._executor.shutdown(wait=True, kill_workers=True)
        self._executor = None

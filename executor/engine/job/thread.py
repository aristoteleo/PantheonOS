import asyncio
import functools
from inspect import iscoroutinefunction
from concurrent.futures import ThreadPoolExecutor

from .base import Job
from .utils import (
    _gen_initializer, create_generator_wrapper, run_async_func
)


class ThreadJob(Job):
    """Job that runs in a thread."""

    def has_resource(self) -> bool:
        """Check if the job has enough resource to run."""
        if self.engine is None:
            return False
        else:
            return (
                super().has_resource() and
                (self.engine.resource.n_thread > 0)
            )

    def consume_resource(self) -> bool:
        """Consume resource for the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_thread -= 1
            return (
                super().consume_resource() and
                True
            )

    def release_resource(self) -> bool:
        """Release resource for the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_thread += 1
            return (
                super().release_resource() and
                True
            )

    async def run_function(self):
        """Run job in thread pool."""
        func = functools.partial(self.func, *self.args, **self.kwargs)
        if iscoroutinefunction(func):
            func = functools.partial(run_async_func, func)
        self._executor = ThreadPoolExecutor(1)
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, func)
        result = await fut
        return result

    async def run_generator(self):
        """Run job as a generator."""
        func = functools.partial(self.func, *self.args, **self.kwargs)
        self._executor = ThreadPoolExecutor(
            1, initializer=_gen_initializer, initargs=(func,))
        result = create_generator_wrapper(self)
        return result

    async def cancel(self):
        """Cancel job."""
        if self.status == "running":
            self._executor.shutdown()
        await super().cancel()

    def clear_context(self):
        """Clear context."""
        self._executor.shutdown()
        self._executor = None

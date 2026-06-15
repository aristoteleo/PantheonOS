from inspect import iscoroutinefunction

from .base import Job
from .utils import create_generator_wrapper


class LocalJob(Job):
    async def run_function(self):
        """Run job in local thread."""
        if iscoroutinefunction(self.func):
            res = await self.func(*self.args, **self.kwargs)
        else:
            res = self.func(*self.args, **self.kwargs)
        return res

    async def run_generator(self):
        """Run job as a generator."""
        return create_generator_wrapper(self)

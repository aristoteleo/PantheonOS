import typing as T
import asyncio
import inspect
from datetime import datetime
from concurrent.futures import Future
import threading

from ..utils import CheckAttrRange, ExecutorError


if T.TYPE_CHECKING:
    from .base import Job

JobStatusType = T.Literal['pending', 'running', 'failed', 'done', 'cancelled']
valid_job_statuses: T.List[JobStatusType] = [
    'pending', 'running', 'failed', 'done', 'cancelled']


class JobStatusAttr(CheckAttrRange):
    valid_range: T.Iterable[JobStatusType] = valid_job_statuses
    attr = "_status"

    def __set__(self, obj: "Job", value: JobStatusType):
        self.check(obj, value)
        old_status = getattr(obj, self.attr)
        setattr(obj, self.attr, value)
        if obj.engine is not None:
            obj.engine.jobs.move_job_store(
                obj, value, old_status)
        if value in ('done', 'failed', 'cancelled'):
            obj.stoped_time = datetime.now()


class InvalidStateError(ExecutorError):
    def __init__(self, job: "Job", valid_status: T.List[JobStatusType]):
        self.valid_status = valid_status
        super().__init__(
            f"Invalid state: {job} is in {job.status} state, "
            f"but should be in {valid_status} state.")


_thread_locals = threading.local()


def _gen_initializer(gen_func, args=tuple(), kwargs={}):  # pragma: no cover
    global _thread_locals
    if "_thread_locals" not in globals():
        # avoid conflict for ThreadJob
        _thread_locals = threading.local()
    _thread_locals._generator = gen_func(*args, **kwargs)


def _gen_next(send_value=None, fut=None):  # pragma: no cover
    if fut is None:
        g = _thread_locals._generator
    else:
        g = fut
    if send_value is None:
        return next(g)
    else:
        return g.send(send_value)


def _gen_anext(send_value=None, fut=None):  # pragma: no cover
    if fut is None:
        g = _thread_locals._generator
    else:
        g = fut
    if send_value is None:
        return asyncio.run(g.__anext__())
    else:
        return asyncio.run(g.asend(send_value))


class GeneratorWrapper():
    """
    wrap a generator in executor pool
    """

    def __init__(self, job: "Job", fut: T.Optional[Future] = None):
        self._job = job
        self._fut = fut
        self._local_res = None


class SyncGeneratorWrapper(GeneratorWrapper):
    """
    wrap a generator in executor pool
    """
    def __iter__(self):
        return self

    def _next(self, send_value=None):
        try:
            if self._job._executor is not None:
                return self._job._executor.submit(
                    _gen_next, send_value, self._fut).result()
            else:
                # create local generator
                if self._local_res is None:
                    self._local_res = self._job.func(
                        *self._job.args, **self._job.kwargs)
                if send_value is not None:
                    return self._local_res.send(send_value)
                else:
                    return next(self._local_res)
        except Exception as e:
            engine = self._job.engine
            if engine is None:
                loop = asyncio.get_event_loop()  # pragma: no cover
            else:
                loop = engine.loop
            if isinstance(e, StopIteration):
                cor = self._job.on_done(self)
            else:
                cor = self._job.on_failed(e)
            fut = asyncio.run_coroutine_threadsafe(cor, loop)
            fut.result()
            raise e

    def __next__(self):
        return self._next()

    def send(self, value):
        return self._next(value)


class AsyncGeneratorWrapper(GeneratorWrapper):
    """
    wrap a generator in executor pool
    """
    def __aiter__(self):
        return self

    async def _anext(self, send_value=None):
        try:
            if self._job._executor is not None:
                fut = self._job._executor.submit(
                    _gen_anext, send_value, self._fut)
                res = await asyncio.wrap_future(fut)
                return res
            else:
                if self._local_res is None:
                    self._local_res = self._job.func(
                        *self._job.args, **self._job.kwargs)
                if send_value is not None:
                    return await self._local_res.asend(send_value)
                else:
                    return await self._local_res.__anext__()
        except Exception as e:
            if isinstance(e, StopAsyncIteration):
                await self._job.on_done(self)
            else:
                await self._job.on_failed(e)
            raise e

    async def __anext__(self):
        return await self._anext()

    async def asend(self, value):
        return await self._anext(value)


def create_generator_wrapper(
        job: "Job", fut: T.Optional[Future] = None) -> GeneratorWrapper:
    if inspect.isasyncgenfunction(job.func):
        return AsyncGeneratorWrapper(job, fut)
    else:
        return SyncGeneratorWrapper(job, fut)


def run_async_func(func, *args, **kwargs):
    return asyncio.run(func(*args, **kwargs))

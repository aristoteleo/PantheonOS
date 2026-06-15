import asyncio
import inspect
import typing as T
from datetime import datetime
from pathlib import Path
from copy import copy
import itertools

import cloudpickle

from .utils import (
    JobStatusAttr, InvalidStateError, JobStatusType,
    ExecutorError, valid_job_statuses, GeneratorWrapper
)
from .condition import Condition, AfterOthers, AllSatisfied
from ..middle.capture import CaptureOut
from ..middle.dir import ChDir
from ..log import logger
from ..base import ExecutorObj
from ..utils import get_callable_name


if T.TYPE_CHECKING:
    from ..core import Engine


class StopResolve(Exception):
    pass


class JobFuture():
    def __init__(self, job_id: str) -> None:
        """A future object for job."""
        self.job_id = job_id
        self._result: T.Optional[T.Any] = None
        self._exception: T.Optional[Exception] = None
        self.done_callbacks: T.List[T.Callable] = []
        self.error_callbacks: T.List[T.Callable] = []

    def result(self) -> T.Any:
        return self._result

    def set_result(self, result: T.Any) -> None:
        self._result = result

    def exception(self) -> T.Optional[Exception]:
        return self._exception

    def set_exception(self, exception: Exception) -> None:
        self._exception = exception

    def add_done_callback(self, callback: T.Callable) -> None:
        self.done_callbacks.append(callback)

    def add_error_callback(self, callback: T.Callable) -> None:
        self.error_callbacks.append(callback)


class Job(ExecutorObj):

    status = JobStatusAttr()
    repr_attrs: T.List[T.Tuple[str, T.Callable[["Job"], T.Any]]] = [
        ("status", lambda self: self.status),
        ("id", lambda self: self.id),
        ("func", lambda self: get_callable_name(self.func)),
        ("condition", lambda self: self.condition),
        ("retry_remain", lambda self: self.retry_remain),
    ]

    func: T.Callable
    condition: T.Optional[Condition]
    retry_remain: int

    def __init__(
            self,
            func: T.Callable,
            args: T.Optional[tuple] = None,
            kwargs: T.Optional[dict] = None,
            callback: T.Optional[T.Callable[[T.Any], None]] = None,
            error_callback: T.Optional[T.Callable[[Exception], None]] = None,
            retries: int = 0,
            retry_time_delta: float = 0.0,
            name: T.Optional[str] = None,
            condition: T.Optional[Condition] = None,
            wait_time_delta: float = 0.01,
            redirect_out_err: T.Union[bool, str] = False,
            change_dir: bool = False,
            **attrs
            ) -> None:
        """Base class for job.

        Args:
            func: The function to be executed.
            args: The positional arguments to be passed to the function.
            kwargs: The keyword arguments to be passed to the function.
            callback: The callback function to be called when the job is done.
            error_callback: The callback function to be called
                when the job is failed.
            retries: The number of retries when the job is failed.
            retry_time_delta: The time delta between retries.
            name: The name of the job.
            condition: The condition of the job.
            wait_time_delta: The time delta between each
                check of the condition.
            redirect_out_err: Whether to redirect the stdout
                and stderr to the log. If is a string, it will be
                used as the path of the log file.
            change_dir: Whether to change the working directory
                to the log directory.
            **attrs: The attributes of the job.
        """
        super().__init__()
        self.future = JobFuture(self.id)
        self.func = func
        self.args = args or tuple()
        self.kwargs = kwargs or {}
        if callback is not None:
            self.future.add_done_callback(callback)
        if error_callback is not None:
            self.future.add_error_callback(error_callback)
        self.retries = retries
        self.retry_remain = retries
        self.retry_time_delta = retry_time_delta
        self.engine: T.Optional["Engine"] = None
        self._status: str = "created"
        self.name = name or func.__name__
        self.attrs = attrs
        self.task: T.Optional[asyncio.Task] = None
        self.condition = condition
        self.wait_time_delta = wait_time_delta
        self.redirect_out_err = redirect_out_err
        self.change_dir = change_dir
        self.created_time: datetime = datetime.now()
        self.submit_time: T.Optional[datetime] = None
        self.stoped_time: T.Optional[datetime] = None
        self._executor: T.Any = None
        self.dep_job_ids: T.List[str] = []

    def __repr__(self) -> str:
        attrs = []
        for attr_name, attr_func in self.repr_attrs:
            attr_value = attr_func(self)
            if bool(attr_value) is False:
                continue
            attrs.append(f"{attr_name}={attr_value}")
        attr_str = " ".join(attrs)
        return f"<{self.__class__.__name__} {attr_str}>"

    def __str__(self) -> str:
        return repr(self)

    def has_resource(self) -> bool:
        """Check if the job has resource to run."""
        if self.engine is None:
            return False
        else:
            return self.engine.resource.n_job > 0

    def consume_resource(self) -> bool:
        """Consume the resource of the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_job -= 1
            return True

    def release_resource(self) -> bool:
        """Release the resource of the job."""
        if self.engine is None:
            return False
        else:
            self.engine.resource.n_job += 1
            return True

    def resolve_dependencies(self) -> None:
        """Resolve args and kwargs
        and auto specify the condition."""
        dep_jobs_ids: T.List[str] = []
        args = itertools.chain(self.args, self.kwargs.values())
        for arg in args:
            if isinstance(arg, JobFuture):
                dep_jobs_ids.append(arg.job_id)
        if len(dep_jobs_ids) > 0:
            after_others = AfterOthers(dep_jobs_ids)
            if self.condition is None:
                self.condition = after_others
            else:
                self.condition = AllSatisfied([self.condition, after_others])
        self.dep_job_ids = dep_jobs_ids

    async def _resolve_arg(self, arg: T.Union[JobFuture, T.Any]) -> T.Any:
        if isinstance(arg, JobFuture):
            assert self.engine is not None
            job = self.engine.jobs.get_job_by_id(arg.job_id)
            if job.status == "done":
                return job.result()
            elif job.status == "failed":
                msg = f"Job {self} cancelled because " \
                      f"of upstream job {job} failed."
                logger.warning(msg)
                await self.cancel()
                raise StopResolve(msg)
            elif job.status == "cancelled":
                msg = f"Job {self} cancelled because " \
                      f"of upstream job {job} cancelled."
                logger.warning(msg)
                await self.cancel()
                raise StopResolve(msg)
            else:  # pragma: no cover
                raise ExecutorError("Unreachable code.")
        else:
            return arg

    async def resolve_args(self):
        """Resolve args and kwargs."""
        if len(self.dep_job_ids) > 0:
            args = []
            kwargs = {}
            for arg in self.args:
                resolved = await self._resolve_arg(arg)
                args.append(resolved)
            for key, value in self.kwargs.items():
                resolved = await self._resolve_arg(value)
                kwargs[key] = resolved
            self.args = tuple(args)
            self.kwargs = kwargs

    def runnable(self) -> bool:
        """Check if the job is runnable.
        Job is runnable if:
        1. engine is not None.
        2. condition is None and condition is satisfied.
        3. has resource."""
        if self.engine is None:
            return False
        if self.condition is not None:
            return self.condition.satisfy(self.engine) and self.has_resource()
        else:
            return self.has_resource()

    async def emit(self) -> asyncio.Task:
        """Emit the job to the engine."""
        logger.info(f"Emit job {self}, watting for run.")
        if self.status != 'pending':
            raise InvalidStateError(self, ['pending'])
        self.resolve_dependencies()
        self.submit_time = datetime.now()
        loop = asyncio.get_running_loop()
        task = loop.create_task(self.wait_and_run())
        self.task = task
        return task

    def process_func(self):
        """Process(decorate) the target func, before run.
        For example, let the func
        change the dir, redirect the stdout and stderr
        before the actual run."""
        cache_dir = self.cache_dir.resolve()
        is_redirect = (self.redirect_out_err is not False)
        if is_redirect and (not isinstance(self.func, CaptureOut)):
            if isinstance(self.redirect_out_err, str):
                path_stdout = Path(self.redirect_out_err)
                path_stderr = Path(self.redirect_out_err)
            else:
                path_stdout = cache_dir / 'stdout.txt'
                path_stderr = cache_dir / 'stderr.txt'
            self.func = CaptureOut(self.func, path_stdout, path_stderr)
        if self.change_dir:
            self.func = ChDir(self.func, cache_dir)

    async def wait_and_run(self):
        """Wait for the condition satisfied and run the job."""
        while True:
            if self.runnable() and self.consume_resource():
                logger.info(f"Start run job {self}")
                self.process_func()
                try:
                    await self.resolve_args()
                except StopResolve:
                    break
                self.status = "running"
                try:
                    res = await self.run()
                    if not isinstance(res, GeneratorWrapper):
                        await self.on_done(res)
                    else:
                        self.future.set_result(res)
                    return res
                except Exception as e:
                    await self.on_failed(e)
                    break
            else:
                await asyncio.sleep(self.wait_time_delta)

    async def run(self):
        """Run the job."""
        if inspect.isgeneratorfunction(self.func) or inspect.isasyncgenfunction(self.func):  # noqa: E501
            return await self.run_generator()
        else:
            return await self.run_function()

    async def run_function(self):  # pragma: no cover
        """Run the job as a function."""
        msg = f"{type(self).__name__} does not implement " \
              "run_function method."
        raise NotImplementedError(msg)

    async def run_generator(self):  # pragma: no cover
        """Run the job as a generator."""
        msg = f"{type(self).__name__} does not implement " \
              "run_generator method."
        raise NotImplementedError(msg)

    async def rerun(self, check_status: bool = True):
        """Rerun the job."""
        _valid_status: T.List[JobStatusType] = ["cancelled", "done", "failed"]
        if check_status and (self.status not in _valid_status):
            raise InvalidStateError(self, _valid_status)
        logger.info(f"Rerun job {self}")
        self.status = "pending"
        await self.emit()

    def _on_finish(self, new_status: JobStatusType = "done"):
        self.status = new_status
        self.release_resource()

    async def on_done(self, res):
        """Callback when the job is done."""
        logger.info(f"Job {self} done.")
        self.future.set_result(res)
        for callback in self.future.done_callbacks:
            if inspect.iscoroutinefunction(callback):
                await callback(res)
            else:
                callback(res)
        self._on_finish("done")

    async def on_failed(self, e: Exception):
        """Callback when the job is failed."""
        logger.error(f"Job {self} failed: {repr(e)}")
        assert self.engine is not None
        if self.engine.print_traceback:
            logger.exception(e)
        self.future.set_exception(e)
        for err_callback in self.future.error_callbacks:
            if inspect.iscoroutinefunction(err_callback):
                await err_callback(e)
            else:
                err_callback(e)
        if self.retry_remain > 0:
            self._on_finish("pending")
            self.retry_remain -= 1
            await asyncio.sleep(self.retry_time_delta)
            await self.rerun(check_status=False)
        else:
            self._on_finish("failed")

    async def cancel(self):
        """Cancel the job."""
        logger.info(f"Cancel job {self}.")
        self.task.cancel()
        if self.status == "running":
            try:
                self.clear_context()
            except Exception as e:  # pragma: no cover
                print(str(e))
            finally:
                self._on_finish("cancelled")
        elif self.status == "pending":
            self.status = "cancelled"

    def clear_context(self):
        """Clear the context of the job."""
        pass

    def result(self) -> T.Any:
        """Get the result of the job."""
        res = self.future.result()
        if not isinstance(res, GeneratorWrapper):
            if self.status != "done":
                raise InvalidStateError(self, ['done'])
        return res

    def exception(self):
        """Get the exception of the job."""
        return self.future.exception()

    def serialization(self) -> bytes:
        """Serialize the job to bytes."""
        job = copy(self)
        job.task = None
        job.engine = None
        job._executor = None
        bytes_ = cloudpickle.dumps(job)
        return bytes_

    @staticmethod
    def deserialization(bytes_: bytes) -> "Job":
        """Deserialize the job from bytes."""
        job: "Job" = cloudpickle.loads(bytes_)
        return job

    async def join(self, timeout: T.Optional[float] = None):
        """Wait for the job done."""
        if self.task is None:
            raise InvalidStateError(self, valid_job_statuses)
        await asyncio.wait([self.task], timeout=timeout)

    async def wait_until(
        self, check_func: T.Callable[["Job"], bool],
        timeout: T.Optional[float] = None
    ):
        """Wait until the check_func return True."""
        total_time = 0.0
        while not check_func(self):
            await asyncio.sleep(self.wait_time_delta)
            total_time += self.wait_time_delta
            if (timeout is not None) and total_time > timeout:
                raise asyncio.TimeoutError

    async def wait_until_status(
            self, status: JobStatusType,
            timeout: T.Optional[float] = None):
        """Wait until the job is in the target status."""
        await self.wait_until(lambda job: job.status == status, timeout)

    @property
    def cache_dir(self) -> T.Optional[Path]:
        """Get the cache dir of the job."""
        if self.engine is None:
            return None
        parent = self.engine.cache_dir
        path = parent / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

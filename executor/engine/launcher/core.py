import typing as T
import inspect
import functools
from copy import copy

from funcdesc import parse_func
from cmd2func.core import Cmd2Func

from ..core import Engine
from ..job import Job, LocalJob, ThreadJob, ProcessJob
from ..job.extend import SubprocessJob, WebappJob
from ..utils import get_callable_name


Job_or_ExtJob = T.Union[T.Type[Job], T.Callable[..., Job]]


job_type_classes: T.Dict[str, Job_or_ExtJob] = {
    'local': LocalJob,
    'thread': ThreadJob,
    'process': ProcessJob,
    'subprocess': SubprocessJob,
    'webapp': WebappJob,
}


try:
    from ..job.dask import DaskJob
    job_type_classes['dask'] = DaskJob
except ImportError:  # pragma: no cover
    pass


JOB_TYPES = T.Literal[
    'local', 'thread', 'process', 'dask',
    'subprocess', 'webapp'
]


_engine: T.Optional[Engine] = None


def get_default_engine() -> Engine:
    """Get the default engine."""
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def set_default_engine(engine: Engine):
    """Set the default engine."""
    global _engine
    _engine = engine


class LauncherBase(object):
    def __init__(
            self, target_func: T.Union[T.Callable, Cmd2Func],
            engine: T.Optional['Engine'] = None,
            job_type: JOB_TYPES = 'process',
            name: T.Optional[str] = None,
            description: T.Optional[str] = None,
            tags: T.Optional[T.List[str]] = None,
            job_attrs: T.Optional[dict] = None,):
        self._engine = engine
        self.target_func = target_func
        self.__signature__ = inspect.signature(target_func)
        self.job_type = job_type
        if isinstance(target_func, Cmd2Func):
            if self.job_type != 'webapp':
                self.job_type = 'subprocess'
        self.desc = parse_func(target_func)
        self.name = name or get_callable_name(target_func)
        self.description = description or self.target_func.__doc__
        functools.update_wrapper(self, target_func)
        self.tags = tags or []
        job_attrs = job_attrs or {}
        self.job_attrs = job_attrs
        self.job_attrs.update({
            'name': self.name,
        })

    @property
    def engine(self) -> Engine:
        """Get the engine of the launcher."""
        if self._engine is None:
            self._engine = get_default_engine()
        return self._engine

    @engine.setter
    def engine(self, engine: Engine):
        """Set the engine of the launcher."""
        self._engine = engine

    def create_job(self, args: tuple, kwargs: dict, **attrs) -> 'Job':
        """Create a job from the launcher."""
        job_attrs = copy(self.job_attrs)
        job_attrs.update(attrs)
        job_class = job_type_classes[self.job_type]
        if isinstance(self.target_func, Cmd2Func):
            assert job_class in (SubprocessJob, WebappJob)
            cmd_or_gen = self.target_func.get_cmd_str(*args, **kwargs)
            if isinstance(cmd_or_gen, str):
                cmd = cmd_or_gen
            else:
                cmd = next(cmd_or_gen)
            job = job_class(cmd, **job_attrs)  # type: ignore
        else:
            if job_class is WebappJob:
                kwargs = copy(kwargs)
                kwargs.update(job_attrs)
                job = job_class(self.target_func, *args, **kwargs)
            else:
                job = job_class(
                    self.target_func, args, kwargs,
                    **job_attrs
                )
        return job

    @staticmethod
    def _fetch_result(job: 'Job') -> T.Any:
        if job.status == "failed":
            raise job.exception()
        elif job.status == "cancelled":
            raise RuntimeError("Job cancelled")
        else:
            return job.result()

    def __call__(
            self, *args: T.Any, **kwargs: T.Any) -> T.Any:  # pragma: no cover
        raise NotImplementedError("Subclasses must implement __call__")


class SyncLauncher(LauncherBase):

    @property
    def async_mode(self):
        """Check if the launcher is in async mode."""
        return False

    def submit(self, *args, **kwargs) -> Job:
        """Submit a job to the engine."""
        job = self.create_job(args, kwargs)
        self.engine.submit(job)
        return job

    def __call__(self, *args, **kwargs) -> T.Any:
        """Submit a job to the engine and wait for the result."""
        job = self.submit(*args, **kwargs)
        self.engine.wait_job(job)
        return self._fetch_result(job)

    def to_async(self) -> "AsyncLauncher":
        """Convert the launcher to async mode."""
        return AsyncLauncher(
            self.target_func, self._engine, self.job_type,
            self.name, self.description, self.tags,
            job_attrs=self.job_attrs,
        )


class AsyncLauncher(LauncherBase):
    @property
    def async_mode(self):
        """Check if the launcher is in async mode."""
        return True

    async def submit(self, *args, **kwargs):
        """Submit a job to the engine."""
        job = self.create_job(args, kwargs)
        await self.engine.submit_async(job)
        return job

    async def __call__(self, *args, **kwargs) -> T.Any:
        """Submit a job to the engine and wait for the result."""
        job = await self.submit(*args, **kwargs)
        await job.join()
        return self._fetch_result(job)

    def to_sync(self) -> "SyncLauncher":
        """Convert the launcher to sync mode."""
        return SyncLauncher(
            self.target_func, self._engine, self.job_type,
            self.name, self.description, self.tags,
            job_attrs=self.job_attrs,
        )


def launcher(
        func: T.Optional[T.Union[T.Callable, Cmd2Func]] = None,
        engine: T.Optional['Engine'] = None,
        async_mode: bool = False,
        job_type: JOB_TYPES = 'process',
        name: T.Optional[str] = None,
        description: T.Optional[str] = None,
        tags: T.Optional[T.List[str]] = None,
        job_attrs: T.Optional[dict] = None):
    """Create a launcher for a function.

    Args:
        func: The function to be launched. If the function is instance of
            [Cmd2Func](https://github.com/Nanguage/cmd2func),
            the launcher will be in subprocess mode,
            will launch `SubprocessJob` on each submit.
        engine: The engine to use. If not specified, the default engine
            will be used.
        async_mode: If True, the launcher will be AsyncLauncher.
        job_type: The job type to use. Default is 'process'.
        name: The name of the launcher.
        description: The description of the launcher.
        tags: The tags of the launcher.
        job_attrs: The attributes for creating the job.
    """
    if func is None:
        return functools.partial(
            launcher, engine=engine, async_mode=async_mode,
            job_type=job_type, name=name,
            description=description, tags=tags,
            job_attrs=job_attrs
        )

    launcher_cls: T.Union[T.Type[AsyncLauncher], T.Type[SyncLauncher]]
    if async_mode:
        launcher_cls = AsyncLauncher
    else:
        launcher_cls = SyncLauncher

    return launcher_cls(
        func, engine, job_type,
        name, description, tags, job_attrs,
    )

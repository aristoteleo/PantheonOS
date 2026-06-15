import typing as T
from dataclasses import dataclass
from pathlib import Path
import asyncio
from threading import Thread
import time
import concurrent.futures

from .base import ExecutorObj
from .job.base import Job
from .manager import Jobs
from .log import logger

if T.TYPE_CHECKING:
    from dask.distributed import Client


@dataclass
class EngineSetting:
    """Engine setting.

    Args:
        max_thread_jobs: Maximum number of thread jobs,
            if not set, no limit.
        max_process_jobs: Maximum number of process jobs,
            if not set, no limit.
        max_dask_jobs: Maximum number of dask jobs,
            if not set, no limit.
        max_jobs: Maximum number of jobs,
            if not set, no limit.
        cache_type: Cache type, "diskcache" or "none".
            If set to "diskcache", will use diskcache package
            to cache job status and result.
            If set to "none", the status and result of job
            will only be stored in memory.
        cache_path: Cache path,
            if not set, will create a cache directory in
            .executor/{engine.id}.
        print_traceback: Whether to print traceback when job failed.
        kwargs_inject_key: Key to inject engine to job kwargs.
            If set, the engine will be injected to job kwargs
            with the key.
            default is "__engine__".
    """
    max_thread_jobs: T.Optional[int] = None
    max_process_jobs: T.Optional[int] = None
    max_dask_jobs: T.Optional[int] = None
    max_jobs: T.Optional[int] = 20
    cache_type: T.Literal["diskcache", "none"] = "none"
    cache_path: T.Optional[str] = None
    print_traceback: bool = True
    kwargs_inject_key: str = "__engine__"


@dataclass
class Resource:
    """Resource of engine.

    Args:
        n_thread: Number of thread jobs.
        n_process: Number of process jobs.
        n_dask: Number of dask jobs.
        n_job: Number of jobs.
    """
    n_thread: T.Union[int, float]
    n_process: T.Union[int, float]
    n_dask: T.Union[int, float]
    n_job: T.Union[int, float]


class Engine(ExecutorObj):
    def __init__(
            self,
            setting: T.Optional[EngineSetting] = None,
            jobs: T.Optional[Jobs] = None,
            loop: T.Optional[asyncio.AbstractEventLoop] = None,
            ) -> None:
        """
        Args:
            setting: Engine setting. Defaults to None.
            jobs: Jobs manager. Defaults to None.
            loop: Event loop. Defaults to None.

        Attributes:
            setting: Engine setting.
            resource: Resource of engine.
            jobs: Jobs manager.
        """
        super().__init__()
        if setting is None:
            setting = EngineSetting()
        self.setting = setting
        self.print_traceback = False
        self.setup_by_setting()
        if jobs is None:
            if self.setting.cache_type == "diskcache":
                jobs = Jobs(self.cache_dir / "jobs")
            else:
                jobs = Jobs()
        self.jobs: Jobs = jobs
        self._dask_client: T.Optional["Client"] = None
        self._loop = loop
        self._loop_thread: T.Optional[Thread] = None

    def __repr__(self) -> str:
        return f"<Engine id={self.id}>"

    def __str__(self) -> str:
        return repr(self)

    @property
    def loop(self):
        """Event loop of engine."""
        if self._loop is None:
            loop = asyncio.new_event_loop()
            logger.info(f"{self} created new event loop.")
            self._loop = loop
        return self._loop

    @loop.setter
    def loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def start(self):
        """Start event loop thread."""
        def run_in_thread(loop: asyncio.AbstractEventLoop):
            logger.info(f"{self} start event loop.")
            asyncio.set_event_loop(loop)
            loop.run_forever()

        if self._loop_thread is None:
            self._loop_thread = Thread(
                target=run_in_thread,
                args=(self.loop,), daemon=True
            )

        if not self._loop_thread.is_alive():
            logger.info(f"{self} start event loop thread.")
            self._loop_thread.start()
        else:
            logger.warning(f"Event loop thread of {self} is already running.")

    def stop(self):
        """Stop event loop thread."""
        loop = self._loop
        if (loop is None) or (self._loop_thread is None):
            logger.warning(f"Event loop thread of {self} is not running.")
            return

        if not self._loop_thread.is_alive():
            logger.warning(f"Event loop thread of {self} is already closed.")
        else:
            logger.info(f"{self} stop event loop.")
            loop.call_soon_threadsafe(loop.stop)
            self._loop_thread.join()
            if self._dask_client is not None:
                asyncio.run_coroutine_threadsafe(
                    self._dask_client.close(), loop)

    def __enter__(self):
        self.start()
        from . launcher.core import set_default_engine
        set_default_engine(self)
        return self

    def __exit__(self, *args):
        self.stop()
        from . launcher.core import set_default_engine
        set_default_engine(None)

    def setup_by_setting(self):
        setting = self.setting
        logger.info(f"Load setting: {setting}")
        self.resource = Resource(
            n_thread=setting.max_thread_jobs or float('inf'),
            n_process=setting.max_process_jobs or float('inf'),
            n_dask=setting.max_dask_jobs or float('inf'),
            n_job=setting.max_jobs or float('inf'),
        )
        self.cache_dir = self.get_cache_dir()
        self.print_traceback = setting.print_traceback

    def submit(self, *jobs: Job):
        """Submit job to engine"""
        if (self._loop_thread is None) or\
           (not self._loop_thread.is_alive()):
            raise RuntimeError(
                f"Event loop thread of {self} is not running."
                " Please use engine as context manager or call engine.start()."
            )
        fut = asyncio.run_coroutine_threadsafe(
            self.submit_async(*jobs), self.loop)
        fut.result()

    async def submit_async(self, *jobs: Job):
        """Asynchronous interface for submit jobs to engine."""
        for job in jobs:
            if job.status == "created":
                job.engine = self
                job._status = "pending"
                func_var_names = job.func.__code__.co_varnames
                if self.setting.kwargs_inject_key in func_var_names:
                    job.kwargs[self.setting.kwargs_inject_key] = self
                self.jobs.add(job)
            else:
                job.status = "pending"
            assert job.engine is self, "Job engine is not this engine."
            await job.emit()

    def remove(self, job: Job):
        """Remove job from engine."""
        if job.status in ('running', 'pending'):
            fut = asyncio.run_coroutine_threadsafe(
                job.cancel(), self.loop)
            fut.result()
        logger.info(f"Remove job from engine: {job}")
        self.jobs.remove(job)

    def cancel(self, *jobs: Job):
        """Cancel a job."""
        futures = []
        for job in jobs:
            fut = asyncio.run_coroutine_threadsafe(
                job.cancel(), self.loop)
            futures.append(fut)
        concurrent.futures.wait(futures)

    async def cancel_all_async(self):
        """Cancel all pending and running jobs."""
        running = self.jobs.running.values()
        pending = self.jobs.pending.values()
        tasks = []
        for job in (pending + running):
            tasks.append(job.cancel())
        await asyncio.gather(*tasks)

    def cancel_all(self):
        """Cancel all pending and running jobs."""
        fut = asyncio.run_coroutine_threadsafe(
            self.cancel_all_async(), self.loop)
        fut.result()

    def wait_job(
            self, job: Job,
            timeout: T.Optional[float] = None,
            ) -> T.Optional[T.Any]:
        """Block until job is finished or timeout.
        Return job result if job is done.

        Args:
            job: Job to wait.
            timeout: Timeout in seconds.
        """
        fut = asyncio.run_coroutine_threadsafe(
            job.join(timeout=timeout), self.loop)
        fut.result()
        if job.status == "done":
            return job.result()
        else:
            return None

    def wait(
            self,
            timeout: T.Optional[float] = None,
            time_delta: float = 0.2,
            select_jobs: T.Optional[T.Callable[[Jobs], T.List[Job]]] = None,
            ):
        """Block until all jobs are finished or timeout.

        Args:
            timeout: Timeout in seconds.
            time_delta: Time interval to check job status.
            select_jobs: Function to select jobs to wait.
        """
        if select_jobs is None:
            select_jobs = (
                lambda jobs: jobs.running.values() + jobs.pending.values()
            )
        total_time = timeout if timeout is not None else float('inf')
        while True:
            n_wait_jobs = len(select_jobs(self.jobs))
            if n_wait_jobs == 0:
                break
            if total_time <= 0:
                break
            time.sleep(time_delta)
            total_time -= time_delta

    async def wait_async(
            self,
            timeout: T.Optional[float] = None,
            time_delta: float = 0.2,
            select_jobs: T.Optional[T.Callable[[Jobs], T.List[Job]]] = None,
            ):
        """Asynchronous interface for wait.
        Block until all jobs are finished or timeout.

        Args:
            timeout: Timeout in seconds.
            time_delta: Time interval to check job status.
            select_jobs: Function to select jobs to wait.
        """
        if select_jobs is None:
            select_jobs = (
                lambda jobs: jobs.running.values() + jobs.pending.values()
            )
        total_time = timeout if timeout is not None else float('inf')
        while True:
            n_wait_jobs = len(select_jobs(self.jobs))
            if n_wait_jobs == 0:
                break
            if total_time <= 0:
                break
            await asyncio.sleep(time_delta)
            total_time -= time_delta

    async def join(
            self,
            jobs: T.Optional[T.List[Job]] = None,
            timeout: T.Optional[float] = None):
        """Join all running and pending jobs."""
        if jobs is None:
            jobs_for_join = (
                self.jobs.running.values() +
                self.jobs.pending.values()
            )
        else:
            jobs_for_join = jobs
        tasks = [
            asyncio.create_task(job.join())
            for job in jobs_for_join
        ]
        if len(tasks) > 0:
            await asyncio.wait(tasks, timeout=timeout)

    def get_cache_dir(self) -> Path:
        """Get cache directory for engine."""
        cache_path = self.setting.cache_path
        if cache_path is not None:
            path = cache_path
        else:
            path = f".executor/{self.id}"
        path_obj = Path(path)
        path_obj.mkdir(parents=True, exist_ok=True)
        return path_obj

    @property
    def dask_client(self):
        from .job.dask import get_default_client
        if self._dask_client is None:
            self._dask_client = get_default_client()
        return self._dask_client

    @dask_client.setter
    def dask_client(self, client: "Client"):
        if not client.asynchronous:
            raise ValueError("Dask client must be asynchronous.")
        self._dask_client = client

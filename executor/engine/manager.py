import typing as T
from pathlib import Path

from diskcache import Cache

from .job.base import Job
from .job.utils import (
    valid_job_statuses, JobStatusType, ExecutorError
)

if T.TYPE_CHECKING:
    from .core import Engine


class JobNotFoundError(ExecutorError):
    """Job not found error."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found.")


class JobStore():
    """Store jobs.

    Attributes:
        cache: The cache on disk(Use diskcache package).
            See:
            [disk-cache's doc](https://github.com/grantjenks/python-diskcache)
            for more details.
        mem: In memory store.
    """

    cache: T.Optional[Cache]
    mem: T.Dict[str, Job]

    def __init__(self, cache_path: T.Optional[Path] = None):
        """Init.

        Args:
            cache_path: Cache path.
        """
        if cache_path is not None:
            self.cache = Cache(str(cache_path))
        else:
            self.cache = None
        self.mem: T.Dict[str, Job] = dict()

    @classmethod
    def load_from_cache(cls, path: Path):
        """Load from cache."""
        store = cls(path)
        store.update_from_cache()
        return store

    def update_from_cache(self, clear_old=False):
        """Update from cache."""
        if clear_old:
            self.mem.clear()
        if self.cache is not None:
            for key in self.cache:
                job = self.get_from_cache(key)
                self.mem[key] = job

    def get_from_cache(self, key: str) -> Job:
        """Get from cache."""
        if self.cache is None:
            raise RuntimeError("No cache")
        bytes_ = self.cache[key]
        job = Job.deserialization(bytes_)
        return job

    def set_to_cache(self, key: str, val: Job):
        """Set job to cache."""
        bytes_ = val.serialization()
        if self.cache is not None:
            self.cache[key] = bytes_

    def __setitem__(self, key: str, val: Job):
        self.mem[key] = val
        if self.cache is not None:
            self.set_to_cache(key, val)

    def __getitem__(self, key: str) -> Job:
        return self.mem[key]

    def __contains__(self, key: str) -> bool:
        return key in self.mem

    def clear(self):
        """Clear all jobs."""
        self.mem.clear()
        if self.cache is not None:
            self.cache.clear()

    def pop(self, key: str) -> Job:
        """Pop a job from store."""
        job = self.mem.pop(key)
        if self.cache is not None:
            self.cache.pop(key)
        return job

    def values(self) -> T.List[Job]:
        """Get all values(Job)."""
        vals = list(self.mem.values())
        return vals

    def keys(self) -> T.List[str]:
        """Get all keys(Job's id)."""
        return list(self.mem.keys())

    def items(self) -> T.List[T.Tuple[str, Job]]:
        """Get all key-value pairs."""
        return list(self.mem.items())

    def __del__(self):
        if self.cache is not None:
            self.cache.close()

    def __len__(self):
        return len(self.mem)


class Jobs:
    """Jobs manager.

    Attributes:
        pending: Pending jobs.
        running: Running jobs.
        done: Done jobs.
        failed: Failed jobs.
        cancelled: Cancelled jobs.
    """
    valid_statuses = valid_job_statuses
    pending: JobStore
    running: JobStore
    done: JobStore
    failed: JobStore
    cancelled: JobStore

    def __init__(self, cache_path: T.Optional[Path] = None):
        self.cache_path = cache_path
        self._stores: T.Dict[str, JobStore] = {}
        s: str
        for s in self.valid_statuses:
            if cache_path is None:
                store = JobStore(None)
            else:
                path = cache_path / s
                if path.exists():
                    store = JobStore.load_from_cache(path)
                else:
                    store = JobStore(path)
            self._stores[s] = store
        self.set_attrs_for_read()

    def update_from_cache(self, clear_old=True):
        """Update jobs from cache."""
        for store in self._stores.values():
            store.update_from_cache(clear_old=clear_old)

    def set_attrs_for_read(self):
        self.pending = self._stores['pending']
        self.running = self._stores['running']
        self.done = self._stores['done']
        self.failed = self._stores['failed']
        self.cancelled = self._stores['cancelled']

    def set_engine(self, engine: "Engine"):
        """Set engine for all jobs."""
        for job in self.all_jobs():
            job.engine = engine

    def clear(self, statuses: T.List[JobStatusType]):
        """Clear jobs by status."""
        for s in statuses:
            self._stores[s].clear()

    def clear_non_active(self):
        """Clear non-active jobs."""
        self.clear(["done", "failed", "cancelled"])

    def clear_all(self):
        """Clear all jobs."""
        self.clear(self.valid_statuses)

    def add(self, job: Job):
        """Add job to store."""
        store = self._stores[job.status]
        store[job.id] = job

    def remove(self, job: Job):
        """Remove job from store."""
        for tp in self.valid_statuses:
            store = self._stores[tp]
            if job.id in store:
                store.pop(job.id)

    def move_job_store(
            self, job: Job,
            new_status: JobStatusType,
            old_status: T.Optional[JobStatusType] = None):
        """Move job to another store."""
        if old_status is None:
            old_status = job.status
        if old_status == new_status:
            return
        old_store = self._stores[old_status]
        new_store = self._stores[new_status]
        new_store[job.id] = old_store.pop(job.id)

    def get_job_by_id(self, job_id: str) -> Job:
        """Get job by id."""
        for status in self.valid_statuses:
            store = self._stores[status]
            if job_id in store:
                return store[job_id]
        raise JobNotFoundError(job_id)

    def __contains__(self, job: T.Union[str, Job]):
        if isinstance(job, Job):
            job_id = job.id
        else:
            job_id = job
        try:
            self.get_job_by_id(job_id)
            return True
        except JobNotFoundError:
            return False

    def all_jobs(self) -> T.List[Job]:
        """Get all jobs."""
        return list(iter(self))

    def __iter__(self):
        """Iterate all jobs."""
        for status in self.valid_statuses:
            store = self._stores[status]
            for job in store.values():
                yield job

    def __len__(self):
        return sum(len(store) for store in self._stores.values())

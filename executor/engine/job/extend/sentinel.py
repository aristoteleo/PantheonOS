import typing as T
import asyncio

from .. import Job, ProcessJob, LocalJob, ThreadJob
from ..condition import Condition


if T.TYPE_CHECKING:
    from ...core import Engine


def SentinelJob(
        func: T.Callable,
        sentinel_condition: Condition,
        job_type: T.Union[str, T.Type[Job]] = "process",
        time_delta: float = 0.01,
        sentinel_attrs: T.Optional[dict] = None,
        **attrs
        ):
    """Submit a job when the sentinel condition is met.

    Args:
        func: The function to be executed.
        sentinel_condition: The sentinel condition.
        job_type: The type of the job.
        time_delta: The time delta between each check
            of the sentinel condition.
        sentinel_attrs: The attributes of the sentinel job.
        **attrs: The attributes of the job.
    """
    sentinel_attrs = sentinel_attrs or {}
    if "name" not in sentinel_attrs:
        sentinel_attrs["name"] = f"sentinel-{func.__name__}"

    base_class: T.Type[Job]
    if isinstance(job_type, str):
        if job_type == "process":
            base_class = ProcessJob
        elif job_type == "thread":
            base_class = ThreadJob
        else:
            base_class = LocalJob
    else:
        base_class = job_type

    async def sentinel(__engine__: "Engine"):
        while True:
            if sentinel_condition.satisfy(__engine__):
                job = base_class(func, **attrs)
                await __engine__.submit_async(job)
            await asyncio.sleep(time_delta)

    sentinel_job = LocalJob(
        sentinel,
        **sentinel_attrs
    )
    return sentinel_job

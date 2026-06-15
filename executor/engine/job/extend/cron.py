import typing as T
from datetime import datetime

from .. import Job
from ..condition import (
    TimeCondition,
    AllSatisfied, AfterTimepoint, BeforeTimepoint,
    EveryPeriod, BeforeClock, AfterClock,
    AfterWeekday, BeforeWeekday
)
from .sentinel import SentinelJob


every = EveryPeriod
daily = EveryPeriod("1d")
weekly = EveryPeriod("7d")
hourly = EveryPeriod("1h")

before_clock = BeforeClock
after_clock = AfterClock

after_weekday = AfterWeekday
before_weekday = BeforeWeekday

after_timepoint = AfterTimepoint
before_timepoint = BeforeTimepoint


def between_clock(start: str, end: str) -> AllSatisfied:
    return AllSatisfied([BeforeClock(end), AfterClock(start)])


def between_weekday(start: str, end: str) -> AllSatisfied:
    return AllSatisfied([BeforeWeekday(end), AfterWeekday(start)])


def between_timepoint(start: datetime, end: datetime) -> AllSatisfied:
    return AllSatisfied([BeforeTimepoint(end), AfterTimepoint(start)])


def CronJob(
        func: T.Callable,
        time_condition: TimeCondition,
        job_type: T.Union[str, T.Type[Job]] = "process",
        time_delta: float = 0.01,
        sentinel_attrs: T.Optional[dict] = None,
        **attrs
        ):
    """Submit a job periodically.

    Args:
        func: The function to be executed.
        time_period: The time period.
        job_type: The type of the job.
        time_delta: The time delta between each
            check of the sentinel condition.
        sentinel_attrs: The attributes of the sentinel job.
        **attrs: The attributes of the job.
    """
    sentinel_attrs = sentinel_attrs or {}
    if "name" not in sentinel_attrs:
        sentinel_attrs["name"] = f"cron-sentinel-{func.__name__}"
    sentinel_job = SentinelJob(
        func,
        time_condition,
        job_type,
        time_delta,
        sentinel_attrs,
        **attrs
    )
    return sentinel_job

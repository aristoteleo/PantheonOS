import typing as T
from datetime import datetime, timedelta
from dataclasses import dataclass

from .utils import JobStatusType

if T.TYPE_CHECKING:
    from ..core import Engine


@dataclass
class Condition():
    """Base class for condition"""

    def satisfy(self, engine: "Engine") -> bool:  # pragma: no cover
        """Check if the condition is satisfied."""
        return True

    def __or__(self, other: "Condition") -> "AnySatisfied":
        return AnySatisfied(conditions=[self, other])

    def __and__(self, other: "Condition") -> "AllSatisfied":
        return AllSatisfied(conditions=[self, other])


@dataclass
class AfterAnother(Condition):
    """Condition that the job is executed after
    another job is done/failed/cancelled.

    Attributes:
        job_id: The id of the job.
        statuses: The statuses of the job that satisfy the condition.
    """

    job_id: str
    statuses: T.Iterable[JobStatusType] = ("done", "failed", "cancelled")

    def satisfy(self, engine) -> bool:
        try:
            another = engine.jobs.get_job_by_id(self.job_id)
        except Exception:
            return False
        if another.status in self.statuses:
            return True
        else:
            return False


@dataclass
class AfterOthers(Condition):
    """Condition that the job is executed after
    other jobs are done/failed/cancelled.

    Attributes:
        job_ids: The ids of the jobs.
        statuses: The statuses of the jobs that satisfy the condition.
        mode: The mode of the condition. If it is 'all', the job is executed
            after all other jobs are done/failed/cancelled. If it is 'any',
            the job is executed after any other job is done/failed/cancelled.
    """

    job_ids: T.List[str]
    statuses: T.Iterable[JobStatusType] = ("done", "failed", "cancelled")
    mode: T.Literal['all', 'any'] = "all"

    def satisfy(self, engine) -> bool:
        other_job_satisfy = []
        for id_ in self.job_ids:
            try:
                job = engine.jobs.get_job_by_id(id_)
            except Exception:
                other_job_satisfy.append(False)
                continue
            s_ = job.status in self.statuses
            other_job_satisfy.append(s_)
        if self.mode == 'all':
            return all(other_job_satisfy)
        else:
            return any(other_job_satisfy)


@dataclass
class Combination(Condition):
    """Base class for combination of conditions.

    Attributes:
        conditions: The sub-conditions.
    """
    conditions: T.List[Condition]


@dataclass
class AllSatisfied(Combination):
    """Condition that the job is executed after all
    sub-conditions are satisfied.

    Attributes:
        conditions: The sub-conditions.
    """

    conditions: T.List[Condition]

    def satisfy(self, engine):
        """Check if the condition is satisfied."""
        return all([c.satisfy(engine) for c in self.conditions])


@dataclass
class AnySatisfied(Combination):
    """Condition that the job is executed after any
    sub-condition is satisfied.

    Attributes:
        conditions: The sub-conditions.
    """

    conditions: T.List[Condition]

    def satisfy(self, engine):
        """Check if the condition is satisfied."""
        return any([c.satisfy(engine) for c in self.conditions])


def _parse_clock_str(time_str: str) -> T.Tuple[int, int, int]:
    hour: int
    minute: int
    second: int
    if time_str.count(":") == 1:
        _hour, _minute = time_str.split(":")
        hour, minute = int(_hour), int(_minute)
        second = 0
    elif time_str.count(":") == 2:
        _hour, _minute, _second = time_str.split(":")
        hour, minute, second = int(_hour), int(_minute), int(_second)
    else:
        hour = int(time_str)
        minute = 0
        second = 0
    return hour, minute, second


def _parse_weekday_str(weekday_str: str) -> int:
    if weekday_str.lower() in ("monday", "mon"):
        return 0
    elif weekday_str.lower() in ("tuesday", "tue"):
        return 1
    elif weekday_str.lower() in ("wednesday", "wed"):
        return 2
    elif weekday_str.lower() in ("thursday", "thu"):
        return 3
    elif weekday_str.lower() in ("friday", "fri"):
        return 4
    elif weekday_str.lower() in ("saturday", "sat"):
        return 5
    elif weekday_str.lower() in ("sunday", "sun"):
        return 6
    raise ValueError(f"Invalid weekday string: {weekday_str}")


def _parse_period_str(period_str: str) -> timedelta:
    if period_str.endswith("d"):
        return timedelta(days=float(period_str[:-1]))
    elif period_str.endswith("h"):
        return timedelta(hours=float(period_str[:-1]))
    elif period_str.endswith("m"):
        return timedelta(minutes=float(period_str[:-1]))
    elif period_str.endswith("s"):
        return timedelta(seconds=float(period_str[:-1]))
    raise ValueError(f"Invalid period string: {period_str}")


@dataclass
class TimeCondition(Condition):
    pass


@dataclass
class EveryPeriod(TimeCondition):
    """Every period.

    Attributes:
        period_str: The period string.
            format: "1d", "1h", "1m", "1s"
        last_submitted_at: The last submitted time.
        immediate: Whether to submit the job immediately.
    """
    period_str: str
    last_submitted_at: T.Optional[datetime] = None
    immediate: bool = False

    def satisfy(self, _) -> bool:
        period = _parse_period_str(self.period_str)
        res: bool
        if self.last_submitted_at is None:
            self.last_submitted_at = datetime.now()
            res = self.immediate
        else:
            res = (datetime.now() - self.last_submitted_at >= period)
        if res:
            self.last_submitted_at = datetime.now()
        return res


@dataclass
class AfterClock(TimeCondition):
    """After a specific clock.

    Attributes:
        time_str: The time string.
            format: "12:00", "12:00:00"
    """
    time_str: str

    def satisfy(self, _) -> bool:
        hour, minute, second = _parse_clock_str(self.time_str)
        now = datetime.now()
        res = (
            (now.hour >= hour) &
            (now.minute >= minute) &
            (now.second >= second)
        )
        return res


@dataclass
class BeforeClock(TimeCondition):
    """Before a specific clock.

    Attributes:
        time_str: The time string.
            format: "12:00", "12:00:00"
    """
    time_str: str

    def satisfy(self, _) -> bool:
        hour, minute, second = _parse_clock_str(self.time_str)
        now = datetime.now()
        res = (
            (now.hour <= hour) &
            (now.minute <= minute) &
            (now.second <= second)
        )
        return res


@dataclass
class AfterWeekday(TimeCondition):
    """After a specific weekday.

    Attributes:
        weekday_str: The weekday string.
            format: "monday", "mon", "tuesday", "tue", "wednesday", "wed",
                "thursday", "thu", "friday", "fri", "saturday", "sat",
                "sunday", "sun"
    """
    weekday_str: str

    def satisfy(self, _) -> bool:
        weekday = _parse_weekday_str(self.weekday_str)
        now = datetime.now()
        res = (now.weekday() >= weekday)
        return res


@dataclass
class BeforeWeekday(TimeCondition):
    """Before a specific weekday.

    Attributes:
        weekday_str: The weekday string.
            format: "monday", "mon", "tuesday", "tue", "wednesday", "wed",
                "thursday", "thu", "friday", "fri", "saturday", "sat",
                "sunday", "sun"
    """
    weekday_str: str

    def satisfy(self, _) -> bool:
        weekday = _parse_weekday_str(self.weekday_str)
        now = datetime.now()
        res = (now.weekday() <= weekday)
        return res


_valid_timepoint_fields = ("year", "month", "day", "hour", "minute", "second")


@dataclass
class AfterTimepoint(TimeCondition):
    """After a timepoint.

    Attributes:
        timepoint: The timepoint.
    """

    timepoint: datetime

    def satisfy(self, _) -> bool:
        return datetime.now() > self.timepoint


@dataclass
class BeforeTimepoint(TimeCondition):
    """Before a timepoint.

    Attributes:
        timepoint: The timepoint.
    """
    timepoint: datetime

    def satisfy(self, _) -> bool:
        return datetime.now() < self.timepoint

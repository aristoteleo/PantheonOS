"""
Memory freshness tracking and staleness warnings.

Tracks memory age via file mtime and provides human-readable
age text and staleness caveats for memories older than 1 day.
"""

from __future__ import annotations

import time


def memory_age_days(mtime: float) -> int:
    """Calculate memory age in days from mtime (unix timestamp)."""
    return max(0, int((time.time() - mtime) / 86_400))


def memory_age_text(mtime: float) -> str:
    """Human-readable age: 'today', 'yesterday', 'N days ago'."""
    days = memory_age_days(mtime)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def staleness_warning(mtime: float) -> str | None:
    """Return staleness caveat for memories older than 1 day, or None."""
    days = memory_age_days(mtime)
    if days <= 1:
        return None
    return (
        f"This memory is {days} days old. "
        "Memories are point-in-time observations, not live state — "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current state before asserting as fact."
    )


def annotate_with_freshness(content: str, mtime: float) -> str:
    """Append freshness caveat to content if memory is stale."""
    warning = staleness_warning(mtime)
    if warning is None:
        return content
    return f"{content}\n\n---\n*{warning}*"

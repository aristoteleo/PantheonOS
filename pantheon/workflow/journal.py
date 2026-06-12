"""Append-only journal with prefix-cache resume semantics (Task 2).

The journal records every ``node()`` call of a deterministic workflow script,
one JSON object per line in ``journal.jsonl``. It powers cheap ``resume`` via a
*longest-unchanged-prefix* cache.

Addressing is ALWAYS by ``node_id`` (an engine-assigned monotonically
increasing int, position in the journal), NEVER by ``label`` (label is
display-only; it may repeat or be empty -- Codex Finding 4).

Resume rule (mirrors Claude Code ``resumeFromRunId``)
-----------------------------------------------------
During resume the script re-executes and issues ``node()`` calls in the same
order, so the Nth call is assigned ``node_id == N`` and corresponds to position
N in the prior journal. For each call the engine asks :meth:`Journal.lookup`
whether a cached result is valid:

* A position is a **hit** iff a prior entry exists at that ``node_id`` AND its
  stored ``key`` equals the recomputed key (skipped entries hit regardless of
  key) AND *every earlier position was also a hit*.
* The **first miss invalidates everything after it**: once any position misses
  (key changed, or no prior entry), that position and all later positions
  re-run -- even if a later position's stored key would coincidentally match.

This is implemented with a single "cache valid up to position P" boundary:
``lookup`` is consultable only for positions strictly before the first miss.
Because calls arrive in ``node_id`` order, the boundary is simply the
``node_id`` of the first lookup that failed to match.

Mutation operations (followed by a resume in the engine layer):

* :meth:`invalidate` -- for ``retry_node``: truncate the entry at ``node_id``
  and everything after it, so the next resume re-runs from there.
* :meth:`skip` -- for ``skip_node``: rewrite the entry at ``node_id`` in place
  as ``status="skipped", result_ref=None``. The position stays a valid cached
  "do nothing" hit; it does NOT cascade.

Determinism: never calls datetime.now/time/random. Mutating rewrites use an
atomic temp-file + ``os.replace`` (mirroring ``storage._atomic_write_text``).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .models import JournalEntry

_BOUNDARY_UNSET = -1


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via a same-dir temp + os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _serialize(entries: list[JournalEntry]) -> str:
    lines = [json.dumps(asdict(e), sort_keys=True) for e in entries]
    return ("\n".join(lines) + "\n") if lines else ""


class Journal:
    """In-memory + on-disk journal bound to a single ``journal.jsonl`` path.

    Construction loads any prior entries (crash/restart recovery). Entries are
    held in ``node_id`` order; index == ``node_id``.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.entries: list[JournalEntry] = self._read()
        # Position of the first lookup miss seen this run; once set, every
        # later lookup also misses (the prefix-cascade boundary).
        self._first_miss: int = _BOUNDARY_UNSET

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Journal":
        """Alias for the constructor; both load prior entries from ``path``."""
        return cls(path)

    # --- persistence ---

    def _read(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []
        entries: list[JournalEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(JournalEntry(**json.loads(line)))
        return entries

    def _rewrite(self) -> None:
        """Atomically rewrite the whole file from ``self.entries``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self.path, _serialize(self.entries))

    # --- prefix-cache lookup ---

    def lookup(self, node_id: int, key: str) -> JournalEntry | None:
        """Return the cached entry at ``node_id`` iff it is a valid prefix hit.

        PRECONDITION: callers MUST invoke this in ascending ``node_id`` order
        (the engine guarantees this); the first-miss cascade correctness
        depends on it.

        Calls arrive in ``node_id`` order. A hit requires that no earlier
        position missed, that a prior entry exists at this position, and that
        either the entry is ``skipped`` (always a hit) or its stored key equals
        ``key``. On any miss we record the boundary so all later lookups miss.
        """
        # Already past the first miss -> cascade: everything after misses.
        if self._first_miss != _BOUNDARY_UNSET and node_id >= self._first_miss:
            return None

        # No prior entry recorded at this position -> miss.
        if node_id < 0 or node_id >= len(self.entries):
            self._mark_miss(node_id)
            return None

        entry = self.entries[node_id]

        # A skipped entry is a cached "do nothing" hit regardless of key.
        if entry.status == "skipped":
            return entry

        if entry.key == key:
            return entry

        self._mark_miss(node_id)
        return None

    def _mark_miss(self, node_id: int) -> None:
        if self._first_miss == _BOUNDARY_UNSET or node_id < self._first_miss:
            self._first_miss = node_id

    # --- mutation ---

    def record(self, entry: JournalEntry) -> None:
        """Append a freshly-executed (or skipped) entry and persist it.

        Entries must be recorded in ``node_id`` order (index == node_id).
        """
        if entry.node_id != len(self.entries):
            raise ValueError(
                f"out-of-order record: expected node_id "
                f"{len(self.entries)}, got {entry.node_id}"
            )
        self.entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")

    def invalidate(self, node_id: int) -> None:
        """Drop the entry at ``node_id`` and every entry after it (retry).

        Truncates in memory and on disk so a reload is consistent: the next
        resume treats ``node_id`` (and beyond) as a miss and re-runs them.
        """
        if node_id < 0 or node_id >= len(self.entries):
            return
        del self.entries[node_id:]
        self._rewrite()
        self._mark_miss(node_id)

    def skip(self, node_id: int) -> None:
        """Rewrite the entry at ``node_id`` as skipped, in place (skip_node).

        Unlike :meth:`invalidate`, the position stays valid: on resume it is a
        cached hit yielding ``status="skipped", result_ref=None``. Does not
        cascade.
        """
        if node_id < 0 or node_id >= len(self.entries):
            raise ValueError(f"no entry at node_id {node_id} to skip")
        old = self.entries[node_id]
        self.entries[node_id] = JournalEntry(
            node_id=old.node_id,
            key=old.key,
            label=old.label,
            status="skipped",
            result_ref=None,
            token_cost=0,
        )
        self._rewrite()

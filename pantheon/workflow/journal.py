"""Order-independent journal with prefix-cache resume semantics (Task 2).

The journal records every ``node()`` call of a deterministic workflow script,
one JSON object per line in ``journal.jsonl``. It powers cheap ``resume`` via a
*longest-unchanged-prefix* cache.

Addressing is ALWAYS by ``node_id`` (an engine-assigned monotonically
increasing int, position in the journal), NEVER by ``label`` (label is
display-only; it may repeat or be empty -- Codex Finding 4).

Order-independence (concurrency correctness)
---------------------------------------------
Under ``parallel``/``pipeline`` nodes COMPLETE in runner-completion order, not
``node_id`` order. The journal therefore tolerates **out-of-order record** and
**out-of-order lookup**, keyed by integer ``node_id``:

* Storage is a ``dict[int, JournalEntry]`` keyed by node_id (NOT a list whose
  index == node_id). On disk it remains append-only JSONL; each line carries its
  node_id, so ``_read`` places parsed entries into the dict by node_id (later
  lines overwrite earlier — this supports skip/invalidate rewrites and re-run
  appends at a vacated id).
* :meth:`record` upserts by ``entry.node_id`` — no strict-append check. Records
  may arrive in any order.
* :meth:`lookup` is order-independent. A position HITS iff a prior-run entry
  exists at that node_id AND (its status is ``skipped`` OR its key equals the
  recomputed key) AND ``node_id < self._first_miss`` (the running prefix
  boundary). ``_first_miss`` is a running MINIMUM updated on every miss.

Residual honesty (why this is safe under concurrency)
-----------------------------------------------------
Because lookups can arrive out of order, a higher-id node may be looked up and
HIT before a lower-id node's miss is observed (the running-min boundary has not
dropped to the lower id yet). This is SAFE because real data dependencies are
captured by the content-hashed ``inputs`` folded into each node's ``key``: a
node that actually depends on a changed upstream node has a changed input hash
→ key mismatch → miss, regardless of the ``_first_miss`` boundary. The
``_first_miss`` cascade is a best-effort conservatism layer for the sequential
backbone; correctness rests on the content keys.

Mutation operations (followed by a resume in the engine layer):

* :meth:`invalidate` -- for ``retry_node``: drop the entry at ``node_id`` AND
  every entry with id >= node_id, so the next resume re-runs from there.
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
    held in a ``dict[int, JournalEntry]`` keyed by ``node_id`` so that record
    and lookup are order-independent.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.entries: dict[int, JournalEntry] = self._read()
        # Lowest node_id at which a lookup miss was seen this run; once set,
        # every lookup at id >= this also misses (the prefix-cascade boundary).
        # Maintained as a running MINIMUM (misses can arrive out of order).
        self._first_miss: int = _BOUNDARY_UNSET
        # node_ids recorded THIS run — a within-run duplicate record is a bug.
        self._recorded_this_run: set[int] = set()

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Journal":
        """Alias for the constructor; both load prior entries from ``path``."""
        return cls(path)

    # --- persistence ---

    def _read(self) -> dict[int, JournalEntry]:
        if not self.path.exists():
            return {}
        entries: dict[int, JournalEntry] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = JournalEntry(**json.loads(line))
            # Later lines overwrite earlier ones at the same node_id, so
            # in-place rewrites (skip) and re-run appends resolve correctly.
            entries[entry.node_id] = entry
        return entries

    def _rewrite(self) -> None:
        """Atomically rewrite the whole file from ``self.entries``.

        Values are serialized sorted by node_id so the on-disk JSONL stays in
        ascending node_id order (stable, human-readable).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = [self.entries[k] for k in sorted(self.entries)]
        _atomic_write_text(self.path, _serialize(ordered))

    # --- prefix-cache lookup ---

    def lookup(self, node_id: int, key: str) -> JournalEntry | None:
        """Return the cached entry at ``node_id`` iff it is a valid prefix hit.

        ORDER-INDEPENDENT: callers may invoke this in ANY ``node_id`` order
        (under ``parallel``/``pipeline`` completion order is not source order).

        A position HITS iff:

        * a prior-run entry exists at ``node_id``, AND
        * that entry is ``skipped`` (always a hit) OR its stored key equals the
          recomputed ``key``, AND
        * ``node_id`` is strictly below the running first-miss boundary.

        On any miss (no entry, or key mismatch) the running-minimum
        ``_first_miss`` boundary is lowered to ``node_id``. See the module
        docstring for why out-of-order lookups remain safe (content keys).
        """
        # Past the running first-miss boundary -> cascade miss.
        if self._first_miss != _BOUNDARY_UNSET and node_id >= self._first_miss:
            return None

        entry = self.entries.get(node_id)

        # No prior entry recorded at this position -> miss.
        if entry is None:
            self._mark_miss(node_id)
            return None

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
        """Upsert a freshly-executed (or skipped) entry and persist it.

        Records may arrive in ANY ``node_id`` order (concurrent completion) —
        the entry is upserted into the dict by ``entry.node_id`` and appended
        as a JSONL line carrying that node_id (append-only fast path; ``_read``
        resolves duplicates by last-line-wins).

        A within-run duplicate record of the same node_id is a bug (two nodes
        would share an addressing id) and raises.
        """
        if entry.node_id in self._recorded_this_run:
            raise ValueError(
                f"duplicate record this run for node_id {entry.node_id}"
            )
        self._recorded_this_run.add(entry.node_id)
        self.entries[entry.node_id] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")

    def recorded_node_ids(self) -> frozenset[int]:
        """Return the node_ids :meth:`record` was called with THIS run (a copy).

        Lets a resume caller distinguish freshly-executed nodes from cache hits
        without reaching into the private ``_recorded_this_run`` set.
        """
        return frozenset(self._recorded_this_run)

    def invalidate(self, node_id: int) -> None:
        """Drop the entry at ``node_id`` and every entry with id >= it (retry).

        Rewrites in memory and on disk so a reload is consistent: the next
        resume treats ``node_id`` (and beyond) as a miss and re-runs them.
        """
        doomed = [k for k in self.entries if k >= node_id]
        if not doomed:
            return
        for k in doomed:
            del self.entries[k]
        self._rewrite()
        self._mark_miss(node_id)

    def skip(self, node_id: int) -> None:
        """Rewrite the entry at ``node_id`` as skipped, in place (skip_node).

        Unlike :meth:`invalidate`, the position stays valid: on resume it is a
        cached hit yielding ``status="skipped", result_ref=None``. Does not
        cascade.
        """
        old = self.entries.get(node_id)
        if old is None:
            raise ValueError(f"no entry at node_id {node_id} to skip")
        self.entries[node_id] = JournalEntry(
            node_id=old.node_id,
            key=old.key,
            label=old.label,
            status="skipped",
            result_ref=None,
            token_cost=0,
        )
        self._rewrite()

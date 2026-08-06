"""An evolved thing and the store that holds them.

`Individual` generalises `Program` in three ways, each demanded by an algorithm the current loop
cannot run:

  - `genome` instead of `snapshot`      -- so ideas and falsifiers are first-class, not code in a
                                            trench coat
  - `parent_ids` instead of `parent_id` -- crossover and multi-parent recombination
  - `anchor_id`                         -- the cross-population edge: which idea this code
                                            implements, which method this falsifier attacks

It deliberately has no `fitness_score()`. Ordering is the method's business (see `Ranking`), and
baking it into the individual is what makes multi-objective and adversarial selection impossible
to add later.

`Store` is a plain container. It is not an archive: it does not decide what to keep, does not bin
anything, and has no notion of elite. Those are search decisions and they belong to the method,
which is why MAP-Elites islands and SimpleTES per-chain DAGs can sit on the same store.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .genome import Genome
from .work import Measurement, new_id


@dataclass
class Individual:
    genome: Genome
    id: str = field(default_factory=lambda: new_id())
    kind: str = ""
    parent_ids: List[str] = field(default_factory=list)
    anchor_id: Optional[str] = None
    generation: int = 0
    order: int = -1
    measurements: List[Measurement] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.kind:
            self.kind = getattr(self.genome, "kind", "code")

    @property
    def parent_id(self) -> Optional[str]:
        """The first parent, for the common single-parent case."""
        return self.parent_ids[0] if self.parent_ids else None

    def content_hash(self) -> str:
        return self.genome.content_hash()

    def measured(self, fidelity: Optional[str] = None) -> Optional[Measurement]:
        """The most recent successful measurement, optionally at a specific fidelity."""
        for m in reversed(self.measurements):
            if m.ok and (fidelity is None or m.fidelity == fidelity):
                return m
        return None

    def metrics(self, fidelity: Optional[str] = None) -> Dict[str, float]:
        m = self.measured(fidelity)
        return dict(m.metrics) if m else {}

    def is_measured(self) -> bool:
        return any(m.ok for m in self.measurements)


@dataclass
class Ranking:
    """How a method orders individuals, without committing to a scalar.

    `order` is best-first. `tiers` groups individuals that the method considers incomparable --
    one tier for a total order, several for a Pareto front (tier 0 is the non-dominated set).
    `scores` is advisory: present when the method has a scalar to report, absent when it does not,
    and never required for selection.
    """

    order: List[str] = field(default_factory=list)
    tiers: List[List[str]] = field(default_factory=list)
    scores: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def by_score(cls, scores: Dict[str, float]) -> "Ranking":
        order = sorted(scores, key=lambda i: -scores[i])
        return cls(order=order, tiers=[order], scores=dict(scores))

    def best(self) -> Optional[str]:
        return self.order[0] if self.order else None


class Store:
    """Everything ever produced, keyed by id, plus the lineage edges.

    Append-mostly and deliberately dumb. Methods keep their own indexes -- an archive, a set of
    chains, a Pareto front -- and persist them through `state_dict`.
    """

    def __init__(self) -> None:
        self._by_id: Dict[str, Individual] = {}
        self._children: Dict[str, List[str]] = {}
        self._anchored: Dict[str, List[str]] = {}
        self._by_hash: Dict[str, str] = {}
        self._next_order = 0

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, ind_id: str) -> bool:
        return ind_id in self._by_id

    def __iter__(self) -> Iterator[Individual]:
        return iter(self._by_id.values())

    def add(self, ind: Individual) -> Individual:
        """Store an individual. Returns the individual now under that id -- which is the existing
        one if an identical genome was already stored, so callers can dedup by identity."""
        h = ind.content_hash()
        seen = self._by_hash.get(h)
        if seen is not None and seen in self._by_id:
            return self._by_id[seen]
        ind.order = self._next_order
        self._next_order += 1
        self._by_id[ind.id] = ind
        self._by_hash[h] = ind.id
        for p in ind.parent_ids:
            self._children.setdefault(p, []).append(ind.id)
        if ind.anchor_id:
            self._anchored.setdefault(ind.anchor_id, []).append(ind.id)
        return ind

    def get(self, ind_id: str) -> Optional[Individual]:
        return self._by_id.get(ind_id)

    def record(self, m: Measurement) -> None:
        ind = self._by_id.get(m.individual_id)
        if ind is not None:
            ind.measurements.append(m)

    def of_kind(self, kind: str) -> List[Individual]:
        return [i for i in self._by_id.values() if i.kind == kind]

    def children(self, ind_id: str) -> List[Individual]:
        return [self._by_id[c] for c in self._children.get(ind_id, []) if c in self._by_id]

    def anchored_on(self, ind_id: str) -> List[Individual]:
        """Individuals whose `anchor_id` points here -- the code hanging off an idea."""
        return [self._by_id[c] for c in self._anchored.get(ind_id, []) if c in self._by_id]

    def ancestors(self, ind_id: str) -> List[Individual]:
        """First-parent chain, nearest first, excluding the individual itself."""
        out, seen = [], {ind_id}
        cur = self._by_id.get(ind_id)
        while cur and cur.parent_ids:
            nxt = self._by_id.get(cur.parent_ids[0])
            if nxt is None or nxt.id in seen:
                break
            out.append(nxt)
            seen.add(nxt.id)
            cur = nxt
        return out

    def find_by_hash(self, h: str) -> Optional[Individual]:
        i = self._by_hash.get(h)
        return self._by_id.get(i) if i else None

    def extend(self, inds: Iterable[Individual]) -> None:
        for i in inds:
            self.add(i)

    def ids(self) -> Sequence[str]:
        return list(self._by_id)

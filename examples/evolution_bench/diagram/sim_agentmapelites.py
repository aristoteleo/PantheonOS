"""An AgentMapElites run, simulated -- and it is a real one.

This runs the actual `AgentMapElites` method through the actual `evolve()` loop against a toy
landscape and records what it decided. Which parent was drawn and from where, which cell a child
lands in, whether it becomes its cell's representative, who it displaces, who migrates -- all of
it is read off the method rather than reimplemented. Only the landscape and the mutation are
invented.

TWO islands, and the video leans on a fact the code guarantees: with a single seed, everything
starts on island 0 (children inherit their parent's island), so island 1 begins EMPTY and is
populated by the first migration. The migration interval is set so that first migration lands
after the acts that teach the single-grid mechanics.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Dict, List, Optional

from pantheon.evolution.core import (Budget, EvolveContext, Individual, Measurement, Produced,
                                     TextGenome)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods.agent_map_elites import AgentMapElites

BINS = 5
ISLANDS = 2
STEPS = 30
MIGRATE_EVERY = 14
"""In children measured, which is what `steps` counts. Acts 1 and 2 use the first ~14 events, so
the run is genuinely single-island for as long as the video is teaching the single-grid story."""
SEED_XY = (0.30, 0.35)


def quality(x: float, y: float) -> float:
    """A ridge, so the grid has somewhere good to find and somewhere dull to fill."""
    ridge = math.exp(-((y - 0.35 - 0.45 * x) ** 2) / 0.045)
    return max(0.05, min(0.98, 0.28 + 0.62 * ridge * (0.45 + 0.55 * x)))


class Jitter:
    """One child per prompt: the parent's descriptors, moved a little."""

    def __init__(self, seed: int = 5):
        self.rng = random.Random(seed)
        self.serial = 0

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
        px, py = (parent.genome.meta["x"], parent.genome.meta["y"]) if parent else SEED_XY
        out = []
        for _ in range(item.k):
            self.serial += 1
            x = min(0.99, max(0.01, px + self.rng.gauss(0, 0.16)))
            y = min(0.99, max(0.01, py + self.rng.gauss(0, 0.16)))
            out.append(Produced(
                # the serial keeps every child a distinct individual: the store deduplicates by
                # genome content, and two children that land on the same descriptors would merge
                genome=TextGenome(text=f"#{self.serial} x={x:.3f} y={y:.3f}", kind=item.kind,
                                  meta={"x": x, "y": y}),
                item_id=item.id, batch_id=item.batch_id, parent_ids=list(item.parent_ids)))
        return out


class Landscape:
    """Reports the objective AND the two descriptors, so the method bins on real metrics."""

    kind = "code"

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        x, y = ind.genome.meta["x"], ind.genome.meta["y"]
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"value": quality(x, y), "complexity": x, "diversity": y,
                                    "fitness_weights": {"value": 1.0}},
                           cost=0.01)


class Recorded(AgentMapElites):
    """The real method, with a note taken every time it decides something."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events: List[Dict[str, Any]] = []
        self._sel: Dict[str, Any] = {}
        self._ctx: Optional[EvolveContext] = None

    def _sample_parent(self, ctx):
        self._ctx = ctx
        p = super()._sample_parent(ctx)
        if p is not None:
            # `from_menu` is "was this a representative at the moment of the draw". The uniform
            # exploration path can also land on a representative, so this slightly overcounts;
            # for narration -- "the parent came off the grid" -- the statement stays true.
            menu_now = set(self.elites.values())
            self._sel = {"parent": p.id, "from_menu": p.id in menu_now,
                         "parent_island": self.island_of.get(p.id),
                         "parent_cell": self._bin(self.coords[p.id]) if p.id in self.coords
                         else None}
        return p

    def _place(self, ctx, ind, island=None):
        self._ctx = ctx
        isl_guess = island if island is not None else self.island_of.get(ind.id, 0)
        held_before = self.elites.get((isl_guess, self._bin(self._features(ind))))
        admitted = super()._place(ctx, ind, island=island)
        # After the fact: `_place` may widen a range and rebuild every bin, so the child's bin is
        # only final once it has run.
        isl = self.island_of[ind.id]
        cell = self._bin(self.coords[ind.id])
        self.events.append({
            "kind": "place", "id": ind.id, "island": isl, "cell": cell,
            "score": self._raw(ctx, ind.id), "admitted": admitted,
            "displaced": held_before if admitted and held_before not in (None, ind.id) else None,
            "grid": self._grid_snapshot(ctx), "best": self._raw(ctx, self.best_id),
            "coverage": self.coverage(), **self._sel,
        })
        self._sel = {}
        return admitted

    def _migrate(self):
        before = dict(self.island_of)
        super()._migrate()
        moved = [(nid, before[nid], self.island_of[nid])
                 for nid in before if self.island_of[nid] != before[nid]]
        if moved and self._ctx is not None:
            self.events.append({"kind": "migrate", "moved": moved,
                                "grid": self._grid_snapshot(self._ctx),
                                "coverage": self.coverage()})

    @staticmethod
    def _raw(ctx, ind_id: Optional[str]) -> float:
        """The objective as the evaluator reported it.

        NOT `fitness`: that normalises each metric to the range observed so far, so the leader
        always scores exactly 1.0 and every cell's colour shifts whenever the range widens. Useful
        for ranking, useless for a picture or a curve.
        """
        ind = ctx.store.get(ind_id) if ind_id else None
        v = ind.metrics().get("value") if ind is not None else None
        return float(v) if isinstance(v, (int, float)) else 0.0

    def _grid_snapshot(self, ctx) -> Dict[tuple, tuple]:
        """`(island, cell) -> (representative id, its raw score)` -- the whole book, both islands.

        The whole book and not just the child's cell, because a range widening rebuilds every bin
        and a migration refiles individuals: cells other than the child's can change under any
        event, and the video diffs consecutive snapshots to stay truthful.
        """
        return {key: (v, self._raw(ctx, v)) for key, v in self.elites.items()}


def simulate():
    method = Recorded(num_islands=ISLANDS, feature_bins=BINS, num_inspirations=1,
                      migration_interval=MIGRATE_EVERY, migration_rate=0.34,
                      exploration_ratio=0.25, llm_weight=0.0, function_weight=1.0, seed=4)
    seed_genome = TextGenome(text="seed", kind="code",
                             meta={"x": SEED_XY[0], "y": SEED_XY[1]})
    asyncio.run(evolve(method=method, variator=Jitter(),
                       evaluators={"code": Landscape()}, seeds=[seed_genome],
                       objective="find the ridge", budget=Budget(max_items=STEPS),
                       concurrency=1))
    return method.events


EVENTS = simulate()
PLACES = [e for e in EVENTS if e["kind"] == "place"]

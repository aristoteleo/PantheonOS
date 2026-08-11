"""A MAP-Elites run, simulated -- and it is a real one.

Unlike the SimpleTES sim, which drives the selector directly, this runs the actual method through
the actual `evolve()` loop against a toy landscape. Nothing about which bin a child lands in,
whether it is admitted, who it displaces, or when islands migrate is reimplemented here: it is all
recorded by hooking `MapElitesIslands` and reading what it decided.

The landscape is invented -- a two-descriptor space where quality is highest along a ridge -- and
so is the mutation. Everything downstream of a measurement is the method's.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Dict, List, Optional

from pantheon.evolution.core import (Budget, EvolveContext, Individual, Measurement, Produced,
                                     TextGenome)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods.map_elites import MapElitesIslands

ISLANDS = 2
BINS = 5
STEPS = 26
MIGRATE_EVERY = 9
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
    """Reports the objective AND the two descriptors, so the method bins on real features."""

    kind = "code"

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        x, y = ind.genome.meta["x"], ind.genome.meta["y"]
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"value": quality(x, y), "complexity": x, "diversity": y,
                                    "fitness_weights": {"value": 1.0}},
                           cost=0.01)


class Recorded(MapElitesIslands):
    """The real method, with a note taken at every decision it makes."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events: List[Dict[str, Any]] = []
        self._ctx: Optional[EvolveContext] = None
        self._parent: Optional[str] = None

    def _sample_parent(self, ctx):
        p = super()._sample_parent(ctx)
        self._parent = p.id if p is not None else None
        return p

    def _place(self, ctx, ind, island=None):
        self._ctx = ctx
        isl = island if island is not None else self.island_of.get(ind.id)
        held_before = self.elites.get((isl, self._bin(self._features(ind)))) if isl is not None \
            else None
        admitted = super()._place(ctx, ind, island=island)
        # After the fact: `_place` may widen a range and rebuild every bin, so the child's bin is
        # only final once it has run.
        isl = self.island_of[ind.id]
        cell = self._bin(self.coords[ind.id])
        self.events.append({
            "kind": "place", "id": ind.id, "parent": self._parent, "island": isl, "cell": cell,
            "score": self._raw(ctx, ind.id), "admitted": admitted,
            "displaced": held_before if admitted and held_before not in (None, ind.id) else None,
            "grid": self._grid_snapshot(ctx), "best": self._raw(ctx, self.best_id),
            "coverage": self.coverage(),
        })
        self._parent = None
        return admitted

    def _migrate(self):
        before = {i: sorted(pop) for i, pop in enumerate(self.islands)}
        super()._migrate()
        moved = [(mid, i, self.island_of[mid])
                 for i, pop in before.items() for mid in pop if self.island_of[mid] != i]
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
        """`(island, cell) -> (holder id, its raw score)`, which is all the picture needs."""
        return {k: (v, self._raw(ctx, v)) for k, v in self.elites.items()}


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

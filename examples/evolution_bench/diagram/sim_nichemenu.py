"""A NicheMenu run, simulated -- and it is a real one.

This runs the actual `NicheMenu` method through the actual `evolve()` loop against a toy landscape
and records what it decided. Which parent was drawn and from where, which cell a child lands in,
whether it becomes its cell's representative, who it displaces -- all of it is read off the method
rather than reimplemented. Only the landscape and the mutation are invented.

One island, deliberately. The video explains the mechanism -- loop, tree, menu -- and islands are
a deployment detail (several menus in parallel, occasionally trading representatives) that gets a
closing sentence, not footage.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import Any, Dict, List, Optional

from pantheon.evolution.core import (Budget, EvolveContext, Individual, Measurement, Produced,
                                     TextGenome)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods.niche_menu import NicheMenu

BINS = 5
STEPS = 26
SEED_XY = (0.30, 0.35)


def quality(x: float, y: float) -> float:
    """A ridge, so the menu has somewhere good to find and somewhere dull to fill."""
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


class Recorded(NicheMenu):
    """The real method, with a note taken every time it decides something."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events: List[Dict[str, Any]] = []
        self._sel: Dict[str, Any] = {}

    def _sample_parent(self, ctx):
        p = super()._sample_parent(ctx)
        if p is not None:
            # `from_menu` is "was this a representative at the moment of the draw". The uniform
            # exploration path can also land on a representative, so this slightly overcounts the
            # menu; for narration -- "the parent came off the menu" -- that statement stays true.
            menu_now = set(self.elites.values())
            self._sel = {"parent": p.id, "from_menu": p.id in menu_now,
                         "parent_cell": self._bin(self.coords[p.id]) if p.id in self.coords
                         else None}
        return p

    def _place(self, ctx, ind, island=None):
        held_before = None
        if self.coords is not None:
            key = (0, self._bin(self._features(ind)))
            held_before = self.elites.get(key)
        admitted = super()._place(ctx, ind, island=island)
        # After the fact: `_place` may widen a range and rebuild every bin, so the child's bin is
        # only final once it has run.
        cell = self._bin(self.coords[ind.id])
        self.events.append({
            "id": ind.id, "cell": cell, "score": self._raw(ctx, ind.id), "admitted": admitted,
            "displaced": held_before if admitted and held_before not in (None, ind.id) else None,
            "grid": self._grid_snapshot(ctx), "best": self._raw(ctx, self.best_id),
            "coverage": self.coverage(), **self._sel,
        })
        self._sel = {}
        return admitted

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
        """`cell -> (representative id, its raw score)` -- single island, so the cell is the key."""
        return {cell: (v, self._raw(ctx, v)) for (_, cell), v in self.elites.items()}


def simulate():
    method = Recorded(num_islands=1, feature_bins=BINS, num_inspirations=1,
                      migration_interval=0, exploration_ratio=0.25,
                      llm_weight=0.0, function_weight=1.0, seed=4)
    seed_genome = TextGenome(text="seed", kind="code",
                             meta={"x": SEED_XY[0], "y": SEED_XY[1]})
    asyncio.run(evolve(method=method, variator=Jitter(),
                       evaluators={"code": Landscape()}, seeds=[seed_genome],
                       objective="find the ridge", budget=Budget(max_items=STEPS),
                       concurrency=1))
    return method.events


EVENTS = simulate()
PLACES = EVENTS

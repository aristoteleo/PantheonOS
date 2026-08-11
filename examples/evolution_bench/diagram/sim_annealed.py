"""An AnnealedIdeaCode run, simulated -- and it is a real one.

The method, the schedule, the selection distribution and the judge's CALIBRATION are all the real
code, driven through the real `evolve()` loop. What is stubbed is exactly one thing: the model call
inside the judge, replaced by a number that correlates with an idea's hidden ceiling and is
deliberately squashed into a narrow band. That squashing is the failure the calibration exists to
undo, so faking it there keeps the interesting half honest.

Invented: an idea's ceiling, and how much of it an implementation reaches.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, List

from pantheon.evolution.core import (Budget, EvolveContext, Individual, Measurement, Produced,
                                     TextGenome)
from pantheon.evolution.core.loop import evolve
from pantheon.evolution.methods.annealed_idea_code import CODE, IDEA, IMPL, AnnealedIdeaCode
from pantheon.evolution.variators.judge import LearnedIdeaJudge

STEPS = 30
SEED_SCORE = 0.42
RAW_LO, RAW_HI = 0.72, 0.93
"""The band the stubbed model insists on using. Every prediction lands in it regardless of how good
the approach is -- which is why a linear rescale cannot fix it and the isotonic fit can."""


class Ideas:
    """Writes ideas and implements them. One child per item."""

    def __init__(self, seed: int = 12):
        self.rng = random.Random(seed)
        self.serial = 0

    async def create(self, ctx: EvolveContext, item) -> List[Produced]:
        self.serial += 1
        if item.kind == IDEA:
            parent = ctx.store.get(item.parent_ids[0]) if item.parent_ids else None
            if parent is not None:                       # REFINE: a variation on a known approach
                ceiling = min(0.97, parent.genome.meta["ceiling"] + self.rng.uniform(-0.03, 0.09))
            else:                                        # NEW: an unrelated line of attack
                ceiling = self.rng.uniform(0.45, 0.93)
            return [Produced(
                genome=TextGenome(text=f"approach #{self.serial}", kind=IDEA,
                                  meta={"ceiling": ceiling}),
                item_id=item.id, batch_id=item.batch_id, parent_ids=list(item.parent_ids),
                anchor_id=item.anchor_id)]

        idea = ctx.store.get(item.meta.get("idea") or item.anchor_id)
        ceiling = idea.genome.meta["ceiling"] if idea is not None else 0.5
        base = float(item.meta.get("base") or SEED_SCORE)
        reach = base + (ceiling - base) * self.rng.uniform(0.35, 1.0)
        score = max(0.05, min(0.99, reach + self.rng.gauss(0, 0.02)))
        return [Produced(
            genome=TextGenome(text=f"program #{self.serial}", kind=CODE,
                              meta={"score": score}),
            item_id=item.id, batch_id=item.batch_id, parent_ids=list(item.parent_ids),
            anchor_id=item.anchor_id)]


class Verifier:
    kind = CODE

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        return Measurement(individual_id=ind.id, fidelity=fidelity,
                           metrics={"combined_score": float(ind.genome.meta["score"]),
                                    "validity": 1.0},
                           cost=0.01)


class StubJudge(LearnedIdeaJudge):
    """The real `Calibration`; only the model call is faked.

    The learnable part of the judge is the calibration -- an isotonic regression from whatever band
    the model uses onto the verifier's scale, with the residual spread as sigma. That is arithmetic
    and it runs here for real. The stub supplies a raw number that ranks approaches correctly and
    is compressed into RAW_LO..RAW_HI, which is the shape the real thing produces and the reason a
    monotone fit is the right tool.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rng = random.Random(7)

    async def measure(self, ctx, ind: Individual, fidelity: str = "full") -> Measurement:
        base = float(self.base_fn(ctx, ind)) if self.base_fn else 0.0
        ceiling = float(ind.genome.meta.get("ceiling", 0.5))
        raw_pred = RAW_LO + (RAW_HI - RAW_LO) * ceiling + self.rng.gauss(0, 0.015)
        raw = raw_pred - base
        return Measurement(individual_id=ind.id, fidelity=fidelity, ok=True,
                           metrics={"idea_base": base, "idea_pred_score": raw_pred,
                                    "idea_raw": raw, "idea_delta_hat": self.cal(raw),
                                    "idea_sigma": self.cal.sigma},
                           artifacts={"calibrated": self.cal.fitted, "n_train": len(self.cal)})


class Recorded(AnnealedIdeaCode):
    """The real method, with a note taken every time it decides something."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events: List[Dict[str, Any]] = []
        self._pending: Dict[str, Dict[str, Any]] = {}

    async def ask(self, ctx, n):
        t = self.progress(ctx)
        ideas, probs = self.select_probs(ctx, t)
        items = await super().ask(ctx, n)
        for it in items:
            self._pending[it.id] = {
                "t": t, "temperature": self.temperature(t), "beta": self.beta(t),
                "mix": self.mix(t), "action": it.meta.get("action"),
                "base": it.meta.get("base"),
                "idea": it.meta.get("idea") or (it.parent_ids[0] if it.parent_ids else None),
                "probs": [(i.id, p) for i, p in zip(ideas, probs)],
            }
        return items

    async def on_measured(self, ctx, ind, m):
        before = len(self.judge.cal) if self.judge else 0
        await super().on_measured(ctx, ind, m)
        rec = self._pending.pop(ind.item_id, None) if hasattr(ind, "item_id") else None
        rec = rec or self._pending.pop(m.item_id, None) if hasattr(m, "item_id") else rec
        ev = dict(rec or {})
        # An implementation's PREDICTION lives on the idea it came from, not on the program: the
        # judge measured the idea, the verifier measured the program. Joining them here is the
        # whole point -- "what was said" against "what happened" is two separate measurements.
        said = {}
        idea = ctx.store.get(ev.get("idea")) if ev.get("idea") else None
        if idea is not None:
            for im in idea.measurements:
                said = {k: v for k, v in im.metrics.items() if k.startswith("idea_")} or said
        ev.update({
            "kind": ind.kind, "id": ind.id,
            "score": self._score(ind) if ind.kind == CODE else None,
            "delta_hat": m.metrics.get("idea_delta_hat", said.get("idea_delta_hat")),
            "raw": m.metrics.get("idea_raw", said.get("idea_raw")),
            "idea_base": said.get("idea_base"),
            "sigma": m.metrics.get("idea_sigma", said.get("idea_sigma")),
            "judge_n": len(self.judge.cal) if self.judge else 0,
            "judge_grew": (len(self.judge.cal) if self.judge else 0) > before,
            "fitted": bool(self.judge.cal.fitted) if self.judge else False,
            "best": max([s for s in (self._score(c) for c in ctx.store.of_kind(CODE))
                         if s is not None] or [0.0]),
            "n_ideas": len(list(ctx.store.of_kind(IDEA))),
        })
        self.events.append(ev)


def simulate():
    judge = StubJudge(objective="toy", n_min=4, prior_sigma=0.15)
    method = Recorded(judge=judge, seed=3, w_new=1.0, w_refine=0.42, w_impl=2.05, gamma=2.0,
                      t0=1.0, t1=0.05, beta0=1.2)
    seeds = [TextGenome(text="seed program", kind=CODE, meta={"score": SEED_SCORE})]
    asyncio.run(evolve(method=method, variator=Ideas(),
                       evaluators={CODE: Verifier(), IDEA: judge},
                       seeds=seeds, objective="toy",
                       budget=Budget(max_items=STEPS), concurrency=1))
    return method.events


EVENTS = simulate()
IMPLS = [e for e in EVENTS if e.get("kind") == CODE]

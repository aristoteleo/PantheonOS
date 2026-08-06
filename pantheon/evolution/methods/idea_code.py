"""Alternating idea and code evolution: two populations, one hanging off the other.

Everything else in this package evolves implementations. Here the search runs at two levels: a
population of *approaches* stated in prose, and, hanging off each of them, a population of
implementations of that approach. The loop alternates -- propose and refine ideas, then implement
and refine code under the surviving ideas, then take what the code learned back to the ideas.

The point is that the two levels fail differently. Code evolution polishes whatever approach it
started from and cannot leave it; a strictly better idea is not reachable by editing the
implementation of a worse one. Idea evolution can jump, but on its own it is unfalsifiable -- prose
that sounds good is cheap. Alternating gives each level the other's correction.

Two design decisions, both of which could have gone the other way:

**An idea has two scores, and they are kept apart.** The *prior* is what a judge thought of the
proposal before anything was built. The *realised* value is the best score of code actually
anchored to it. Ranking uses the realised value once it exists and the prior only until then, so a
plausible-sounding idea that implements badly is demoted by evidence rather than by taste. Keeping
both is not bookkeeping: the gap between them measures how much the idea evaluator is worth, which
is exactly the thing a reader will doubt.

**Code subtrees are not discarded when the ideas move on.** A new idea starts a fresh line of
implementations, but a child may take its *code* parent from the best implementation of an older
idea while being *anchored* to the new one. That is only expressible because descent and
aboutness are separate edges -- `parent_ids` says what it was derived from, `anchor_id` says what
it is an implementation of -- and it is what lets a strong implementation carry across a change of
approach instead of being thrown away with the idea that produced it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext

IDEA = "idea"
CODE = "code"


def _metric(ind: Optional[Individual], key: str) -> Optional[float]:
    if ind is None:
        return None
    v = ind.metrics().get(key)
    return float(v) if isinstance(v, (int, float)) else None


@dataclass
class _Round:
    """Where the search is: which phase, and how much of it is left."""

    index: int = 0
    phase: str = IDEA
    remaining: int = 0


class IdeaCodeAlternating(BaseMethod):
    """Evolve approaches and implementations in alternating phases."""

    name = "idea_code_alternating"

    def __init__(
        self,
        ideas_per_round: int = 4,
        code_per_idea: int = 2,
        ideas_kept: int = 3,
        k_ideas: int = 2,
        k_code: int = 1,
        score_key: str = "combined_score",
        prior_key: str = "idea_score",
        realised_key: str = "idea_realised",
        carry_best_code: bool = True,
        seed: int = 0,
    ):
        self.ideas_per_round = ideas_per_round
        self.code_per_idea = code_per_idea
        self.ideas_kept = ideas_kept
        self.k_ideas = k_ideas
        self.k_code = k_code
        self.score_key = score_key
        self.prior_key = prior_key
        self.realised_key = realised_key
        self.carry_best_code = carry_best_code
        """Whether a new idea's first implementation may start from the best code seen so far.

        On: an approach change keeps the engineering that already works, which is what a person
        would do. Off: every idea is implemented from the seed, which is slower but makes the
        comparison between ideas clean -- no idea inherits another's head start. Worth being able
        to turn off, because with it on a late idea's code looks better partly because it started
        better.
        """
        self.rng = random.Random(seed)

        self.round = _Round(index=0, phase=IDEA, remaining=ideas_per_round)
        self.ideas: List[str] = []
        self.open_ideas: Dict[str, int] = {}      # batch_id -> pending count
        self.open_code: Dict[str, str] = {}       # batch_id -> anchor idea id
        self.seed_code: Optional[str] = None
        self.history: List[Dict[str, Any]] = []

    # ---- scoring ---------------------------------------------------------

    def realised(self, ctx: EvolveContext, idea_id: str) -> Optional[float]:
        """Best score among implementations anchored to this idea. Evidence, not opinion."""
        scores = [s for s in (_metric(c, self.score_key)
                              for c in ctx.store.anchored_on(idea_id)) if s is not None]
        return max(scores) if scores else None

    def prior(self, ctx: EvolveContext, idea_id: str) -> float:
        """The judge's score, read from the measurement that carries it.

        Searched rather than taken from `metrics()`, which returns the LATEST measurement -- and
        once a realised value has been written back, the latest is no longer the judge's.
        """
        ind = ctx.store.get(idea_id)
        if ind is None:
            return 0.0
        for m in ind.measurements:
            v = m.metrics.get(self.prior_key)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0

    def _record_realised(self, ctx: EvolveContext, idea_id: str) -> None:
        """Write the realised value back onto the idea as a second measurement.

        It is a derivation -- the max over the idea's implementations, which stays authoritative
        and is what `realised()` recomputes. Recording it anyway costs nothing and buys three
        things the derivation cannot: it survives into the checkpoint, it shows up in a report
        without the reader having to know how to recompute it, and another method reading this
        store later sees what each approach was worth instead of only what a judge guessed.

        Appended only when the number moves, so an idea with five implementations carries the
        history of how its standing changed rather than five copies of the same row.
        """
        ind = ctx.store.get(idea_id)
        r = self.realised(ctx, idea_id)
        if ind is None or r is None:
            return
        n = len(ctx.store.anchored_on(idea_id))
        for m in reversed(ind.measurements):
            if self.realised_key in m.metrics:
                if (m.metrics[self.realised_key] == r
                        and m.metrics.get("implementations") == n):
                    return
                break
        ind.measurements.append(Measurement(
            individual_id=idea_id, fidelity="realised", ok=True,
            metrics={self.realised_key: r, "implementations": float(n),
                     self.prior_key: self.prior(ctx, idea_id)},
        ))

    def idea_value(self, ctx: EvolveContext, idea_id: str) -> float:
        """Realised value once anything has been built; the judge's prior only until then."""
        r = self.realised(ctx, idea_id)
        return r if r is not None else self.prior(ctx, idea_id)

    def best_code(self, ctx: EvolveContext) -> Optional[Individual]:
        best, best_s = None, None
        for ind in ctx.store.of_kind(CODE):
            s = _metric(ind, self.score_key)
            if s is not None and (best_s is None or s > best_s):
                best, best_s = ind, s
        return best

    def live_ideas(self, ctx: EvolveContext) -> List[str]:
        """The ideas worth implementing: the top `ideas_kept` by current value."""
        ranked = sorted(self.ideas, key=lambda i: -self.idea_value(ctx, i))
        return ranked[: self.ideas_kept]

    # ---- lifecycle -------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        for s in seeds:
            if s.kind == CODE and self.seed_code is None:
                self.seed_code = s.id
            elif s.kind == IDEA:
                self.ideas.append(s.id)
        self.round = _Round(index=0, phase=IDEA, remaining=self.ideas_per_round)

    # ---- the loop --------------------------------------------------------

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        if self.round.phase == IDEA:
            return self._ask_ideas(ctx, n)
        return self._ask_code(ctx, n)

    def _ask_ideas(self, ctx: EvolveContext, n: int) -> List[Create]:
        """Propose approaches. Later rounds refine the ones that survived, and are told what
        their implementations actually scored -- which is the only channel by which code
        evolution can correct the ideas."""
        want = min(n, self.round.remaining - sum(self.open_ideas.values()))
        items: List[Create] = []
        live = self.live_ideas(ctx)
        for _ in range(max(0, want)):
            parent = self.rng.choice(live) if live else None
            item = Create(
                kind=IDEA,
                parent_ids=[parent] if parent else [],
                k=self.k_ideas,
                context=PromptContext(
                    instruction=ctx.objective,
                    parents=[ctx.store.get(parent)] if parent else [],
                    history=self._idea_history(ctx),
                    extra={"round": self.round.index, "phase": IDEA},
                ),
                meta={"round": self.round.index, "phase": IDEA},
            )
            self.open_ideas[item.batch_id] = self.k_ideas
            items.append(item)
        return items

    def _ask_code(self, ctx: EvolveContext, n: int) -> List[Create]:
        """Implement or refine code under each live idea."""
        items: List[Create] = []
        live = self.live_ideas(ctx)
        if not live:
            return items
        busy = set(self.open_code.values())
        for idea_id in live:
            if len(items) >= n or self.round.remaining - len(self.open_code) <= 0:
                break
            if idea_id in busy:
                continue
            idea = ctx.store.get(idea_id)
            under = ctx.store.anchored_on(idea_id)
            parent = self._code_parent(ctx, under)
            item = Create(
                kind=CODE,
                parent_ids=[parent.id] if parent else [],
                anchor_id=idea_id,
                k=self.k_code,
                context=PromptContext(
                    instruction=self._code_instruction(ctx, idea),
                    parents=[parent] if parent else [],
                    history=self._code_history(ctx, idea_id),
                    extra={"idea": idea.genome.render() if idea else "",
                           "round": self.round.index, "phase": CODE},
                ),
                meta={"round": self.round.index, "phase": CODE, "idea": idea_id},
            )
            self.open_code[item.batch_id] = idea_id
            items.append(item)
            busy.add(idea_id)
        return items

    def _code_parent(self, ctx: EvolveContext, under: List[Individual]) -> Optional[Individual]:
        """The best implementation of this idea; failing that, the best code anywhere.

        Falling back across ideas is what `carry_best_code` controls. It is the difference between
        "try this approach from scratch" and "apply this approach to what already works", and the
        second is both what a person would do and a confound when comparing ideas.
        """
        scored = [(s, c) for c, s in ((c, _metric(c, self.score_key)) for c in under)
                  if s is not None]
        if scored:
            return max(scored, key=lambda t: t[0])[1]
        if self.carry_best_code:
            best = self.best_code(ctx)
            if best is not None:
                return best
        return ctx.store.get(self.seed_code) if self.seed_code else None

    def _code_instruction(self, ctx: EvolveContext, idea: Optional[Individual]) -> str:
        text = idea.genome.render() if idea else ""
        return (f"{ctx.objective}\n\n## The approach to implement\n{text}\n\n"
                "Implement this approach. If the current code follows a different approach, "
                "change it to follow this one rather than making an unrelated improvement.")

    def _idea_history(self, ctx: EvolveContext) -> str:
        """What each idea's implementations actually scored -- the feedback loop's whole payload."""
        rows = []
        for iid in sorted(self.ideas, key=lambda i: -self.idea_value(ctx, i))[:6]:
            ind = ctx.store.get(iid)
            if ind is None:
                continue
            r = self.realised(ctx, iid)
            head = (ind.genome.render() or "").strip().splitlines()
            label = head[0][:110] if head else "(no text)"
            rows.append(
                f"- {label}\n    judged {self.prior(ctx, iid):.3f}; "
                + (f"implemented and measured {r:.4f}" if r is not None
                   else "never implemented"))
        return "\n".join(rows)

    def _code_history(self, ctx: EvolveContext, idea_id: str) -> str:
        rows = []
        for c in sorted(ctx.store.anchored_on(idea_id),
                        key=lambda c: -(_metric(c, self.score_key) or -1e9))[:4]:
            s = _metric(c, self.score_key)
            rows.append(f"- #{c.order}: {self.score_key}="
                        f"{s:.4f}" if s is not None else f"- #{c.order}: unscored")
        return "\n".join(rows)

    # ---- feedback --------------------------------------------------------

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        if ind.kind == IDEA:
            if ind.id not in self.ideas:
                self.ideas.append(ind.id)
            self._close(self.open_ideas, m.batch_id)
        else:
            if ind.anchor_id:
                # an implementation just landed: the approach it implements is now worth
                # something measured, and that supersedes what it was judged to be worth
                self._record_realised(ctx, ind.anchor_id)
            self._close(self.open_code, m.batch_id, single=True)
        self._advance(ctx)

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        self._close(self.open_ideas, f.batch_id)
        self._close(self.open_code, f.batch_id, single=True)
        self._advance(ctx)

    @staticmethod
    def _close(book: Dict[str, Any], batch_id: str, single: bool = False) -> None:
        if batch_id not in book:
            return
        if single:
            book.pop(batch_id, None)
            return
        book[batch_id] -= 1
        if book[batch_id] <= 0:
            book.pop(batch_id, None)

    def _advance(self, ctx: EvolveContext) -> None:
        """Count down the current phase and switch when it is spent.

        The phase ends on work *completed*, not work dispatched, so the code phase always starts
        with ideas that have been measured and the idea phase always starts knowing what the last
        round's code achieved. Switching on dispatch would let a phase begin on stale information.
        """
        self.round.remaining -= 1
        if self.round.remaining > 0 or self.open_ideas or self.open_code:
            return
        if self.round.phase == IDEA:
            live = self.live_ideas(ctx)
            self.round = _Round(index=self.round.index, phase=CODE,
                                remaining=max(1, len(live) * self.code_per_idea))
        else:
            for i in self.ideas:
                self._record_realised(ctx, i)
            self.history.append({
                "round": self.round.index,
                "ideas": [{"id": i, "prior": self.prior(ctx, i),
                           "realised": self.realised(ctx, i)} for i in self.ideas],
            })
            self.round = _Round(index=self.round.index + 1, phase=IDEA,
                                remaining=self.ideas_per_round)

    # ---- reporting -------------------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = CODE) -> Ranking:
        if kind == IDEA:
            return Ranking.by_score({i: self.idea_value(ctx, i) for i in self.ideas})
        scores = {c.id: s for c, s in
                  ((c, _metric(c, self.score_key)) for c in ctx.store.of_kind(CODE))
                  if s is not None}
        return Ranking.by_score(scores)

    def prior_vs_realised(self, ctx: EvolveContext) -> List[Dict[str, Any]]:
        """Every idea's judged prior next to what its code actually achieved.

        The headline diagnostic of this method: if the two are uncorrelated the idea evaluator is
        decoration, and the alternation is only worth its cost because of the code phase.
        """
        out = []
        for iid in self.ideas:
            ind = ctx.store.get(iid)
            history = [m.metrics.get(self.realised_key) for m in (ind.measurements if ind else [])
                       if self.realised_key in m.metrics]
            out.append({
                "id": iid,
                "prior": self.prior(ctx, iid),
                "realised": self.realised(ctx, iid),
                "implementations": len(ctx.store.anchored_on(iid)),
                "realised_history": history,
                "text": (ind.genome.render()[:160] if ind else ""),
            })
        return out

    # ---- the operators this algorithm is defined with ---------------------

    def default_variator(self, **kw):
        from ..variators.idea import IdeaCodeVariator

        return IdeaCodeVariator(**kw)

    # ---- persistence -----------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {
            "round": {"index": self.round.index, "phase": self.round.phase,
                      "remaining": self.round.remaining},
            "ideas": list(self.ideas),
            "seed_code": self.seed_code,
            "history": list(self.history),
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        r = state.get("round", {})
        self.round = _Round(index=r.get("index", 0), phase=r.get("phase", IDEA),
                            remaining=r.get("remaining", self.ideas_per_round))
        self.ideas = list(state.get("ideas", []))
        self.seed_code = state.get("seed_code")
        self.history = list(state.get("history", []))

    def reconcile(self, ctx: EvolveContext) -> None:
        self.ideas = [i for i in self.ideas if i in ctx.store]
        if self.seed_code and self.seed_code not in ctx.store:
            self.seed_code = None
        # batches in flight at checkpoint time will never resolve
        self.open_ideas.clear()
        self.open_code.clear()

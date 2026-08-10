"""Idea and code evolved together, on one scale, under an annealed schedule.

This replaces `IdeaCodeAlternating`, which had three defects that measurement -- not taste --
established:

  1. It sorted the judge's `[0,1]` opinion together with measured objective values. On Erdos the
     two ranges were disjoint (prior 0.80-0.92, realised 0.50-0.62), so every unjudged idea
     outranked every measured one and the top-k cutoff never bound.
  2. Because of (1) the judge ablation compared *no selection* against *random selection* against
     *frozen selection*. The judge's ranking quality was never on trial.
  3. Infeasible programs were being read as scores of zero. Those zeros made the spread within
     one idea's implementations look as large as the spread between ideas, so the quantity a
     judge was asked to predict appeared to be mostly noise.

     Defect 3 was originally diagnosed as a property of the *operator* -- a blind completion that
     cannot run what it writes. Measurement says otherwise, and the correction is recorded here
     because the wrong version of it would justify an expensive change that buys nothing:

       infeasible rate    blind completion 7/96 = 7.3%    coding agent 12/90 = 13.3%
       within/between     blind completion 0.24           coding agent 0.09
                          (median spreads, FEASIBLE implementations only)

     A coding agent that runs the evaluator before submitting does **not** submit fewer infeasible
     programs. And once the zeros are excluded, within-idea spread was always about four times
     smaller than between-idea spread -- the signal a judge needs was there the whole time, buried
     under a handful of zeros. What fixed Defect 3 is the feasibility rule in `_score`, not the
     operator.

The fixes are structural rather than parametric:

  **One scale.** The judge predicts a gain on the verifier's own scale (see `LearnedIdeaJudge`),
  so `mu(I)` has the same units whether it comes from a prediction or a measurement. Defect 1
  cannot recur by definition.

  **Soft selection instead of a cutoff.** Ideas are sampled from a Boltzmann distribution whose
  temperature anneals, so nothing is permanently starved and convergence is what the schedule
  does rather than what a `top-k` truncation does. `ideas_kept` is gone.

  **Marginal contribution.** An idea is credited with what it added to the code it started from,
  not with the absolute score of code that may have been good before it arrived.

  **Feasibility hygiene.** An infeasible program is not a score of zero, in the selection pool or
  in the judge's training set. It reports that the generator failed, and reading it as evidence
  about the approach is what made the idea level look unlearnable.

The schedule, with `t = spent/budget`:

    T(t)    = T0 (T1/T0)^t            selection temperature: near-uniform, then argmax
    beta(t) = beta0 (1 - t)           optimism about ideas nobody has built
    pi(.|t) prop (w_new (1-t)^gamma, w_refine, w_impl)

One annealed term drives all three behaviours. As `u_new` decays the mix slides to REFINE+IMPL,
and by then `T` is low enough that both land on the same idea -- deepening the winner in thought
and in implementation at once.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pantheon.utils.log import logger

from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext

IDEA, CODE = "idea", "code"
NEW, REFINE, IMPL = "NEW", "REFINE", "IMPL"


class AnnealedIdeaCode(BaseMethod):
    """Anneals from proposing approaches to implementing the best one."""

    name = "annealed_idea_code"

    def __init__(
        self,
        *,
        t0: float = 1.0,
        t1: float = 0.05,
        beta0: float = 1.2,
        gamma: float = 2.0,
        w_new: float = 1.0,
        w_refine: float = 0.42,
        w_impl: float = 2.05,
        k_ideas: int = 1,
        k_code: int = 1,
        score_key: str = "combined_score",
        valid_key: str = "validity",
        sigma_impl: float = 0.05,
        prior_sigma: float = 0.15,
        norm: str = "minmax",
        judge: Any = None,
        refit_every: int = 1,
        seed: int = 0,
    ):
        self.t0, self.t1, self.beta0, self.gamma = t0, t1, beta0, gamma
        self.w_new, self.w_refine, self.w_impl = w_new, w_refine, w_impl
        self.k_ideas, self.k_code = k_ideas, k_code
        self.score_key, self.valid_key = score_key, valid_key
        self.sigma_impl, self.prior_sigma = sigma_impl, prior_sigma
        self.norm = norm
        """`minmax` is the published schedule and the default.

        It has a known degeneracy: min-max maps the extremes of the candidate set to 0 and 1
        whatever the true spread, so with two candidates the softmax sees a full unit of
        difference no matter how close they are and the run is greedy exactly when it should be
        broadest. `absolute` divides by an observed score scale instead, which keeps near-equal
        candidates near-equally likely. Left off by default so the documented algorithm is what
        runs unless someone chooses otherwise.
        """
        self.refit_every = refit_every
        self.judge = judge
        if judge is not None and getattr(judge, "base_fn", None) is None:
            # The judge predicts a gain over a starting point, and only the method knows how
            # inheritance decides what that starting point is.
            judge.base_fn = lambda ctx, ind: self.base_of_idea(ctx, ind)
        self.rng = random.Random(seed)
        self.seed_code: Optional[str] = None
        self.issued_base: Dict[str, float] = {}   # batch_id -> the score its parent scored
        self.trained: List[str] = []              # idea ids already in the judge's training set
        self.history: List[Dict[str, Any]] = []

    # ---- schedule --------------------------------------------------------

    def progress(self, ctx: EvolveContext) -> float:
        """`t`, taken from whichever budget dimension is furthest along.

        Reading only `max_items` would leave `t` pinned at 0 for a run bounded by cost or time,
        and an annealing schedule that never anneals is a silent failure rather than a loud one.
        """
        b = ctx.budget
        fr = []
        if b.max_items:
            fr.append(b.items_used / b.max_items)
        if b.max_cost:
            fr.append(b.cost_used / b.max_cost)
        if b.max_seconds:
            fr.append(b.seconds_used / b.max_seconds)
        return min(1.0, max(fr)) if fr else 0.0

    def temperature(self, t: float) -> float:
        return self.t0 * (self.t1 / self.t0) ** t

    def beta(self, t: float) -> float:
        return self.beta0 * (1.0 - t)

    def mix(self, t: float) -> Tuple[float, float, float]:
        u = (self.w_new * (1.0 - t) ** self.gamma, self.w_refine, self.w_impl)
        s = sum(u) or 1.0
        return (u[0] / s, u[1] / s, u[2] / s)

    # ---- reading the store ------------------------------------------------

    def _score(self, ind: Optional[Individual]) -> Optional[float]:
        """The score of a program, or None if it is missing or infeasible.

        Infeasible is not zero. A program that violated a constraint tells you the generator
        failed, not that the approach is worthless, and letting it into either the selection
        pool or the judge's training set is how implementation noise gets mistaken for idea
        quality.
        """
        if ind is None:
            return None
        for m in reversed(ind.measurements):
            if not m.ok:
                continue
            if m.metrics.get(self.valid_key, 1.0) <= 0:
                return None
            v = m.metrics.get(self.score_key)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    def own_scores(self, ctx: EvolveContext, idea_id: str) -> List[float]:
        """Feasible scores of C(I) -- what this idea itself produced."""
        return [s for s in (self._score(c) for c in ctx.store.anchored_on(idea_id))
                if s is not None]

    def pool_best(self, ctx: EvolveContext, idea_id: Optional[str]
                  ) -> Tuple[Optional[float], Optional[Individual]]:
        """Best program in C*(I): this idea's own, then everything up its ancestry.

        This is the inheritance rule. Walking the *descent* edge rather than the anchor edge is
        deliberate -- a refinement is about the same line of thought as its parent, so the code
        that line has already produced is the right place for it to start.
        """
        best_s, best_c = None, None
        cur, seen = idea_id, set()
        while cur and cur not in seen:
            seen.add(cur)
            for c in ctx.store.anchored_on(cur):
                s = self._score(c)
                if s is not None and (best_s is None or s > best_s):
                    best_s, best_c = s, c
            node = ctx.store.get(cur)
            cur = node.parent_ids[0] if (node and node.parent_ids) else None
        if best_c is None and self.seed_code:
            # The seed is a legal place to start from even when its own score is unusable -- a
            # missing metric or a broken evaluator run says nothing about whether the program can
            # be edited. Returning `(None, seed)` keeps those two questions apart; conflating them
            # left every implementation with no parent at all and cost a whole run.
            seed = ctx.store.get(self.seed_code)
            if seed is not None:
                return self._score(seed), seed
        return best_s, best_c

    def base_of_idea(self, ctx: EvolveContext, idea: Individual) -> float:
        """The score an idea starts from: the best its parent line ever reached."""
        par = idea.parent_ids[0] if idea.parent_ids else None
        if par:
            s, _ = self.pool_best(ctx, par)
            if s is not None:
                return s
        seed = ctx.store.get(self.seed_code) if self.seed_code else None
        s = self._score(seed)
        return s if s is not None else 0.0

    def delta_hat(self, idea: Individual) -> float:
        for m in idea.measurements:
            v = m.metrics.get("idea_delta_hat")
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0

    def judged_sigma(self, idea: Individual) -> float:
        for m in idea.measurements:
            v = m.metrics.get("idea_sigma")
            if isinstance(v, (int, float)):
                return float(v)
        return self.prior_sigma

    # ---- the value function -----------------------------------------------

    def mu(self, ctx: EvolveContext, idea: Individual) -> float:
        """base(I) + Delta(I) once anything is built, base(I) + Delta_hat(I) until then.

        The measured branch simplifies: `base + (own_best - base)` is `own_best`. Keeping the
        formula in that form is the point -- both branches are a score on the verifier's scale,
        so the two are comparable and no ordering pathology is possible.
        """
        own = self.own_scores(ctx, idea.id)
        if own:
            return max(own)
        return self.base_of_idea(ctx, idea) + self.delta_hat(idea)

    def sigma(self, ctx: EvolveContext, idea: Individual) -> float:
        n = len(self.own_scores(ctx, idea.id))
        return self.sigma_impl / math.sqrt(n) if n else self.judged_sigma(idea)

    def acquisition(self, ctx: EvolveContext, t: float) -> Tuple[List[Individual], List[float]]:
        ideas = sorted(ctx.store.of_kind(IDEA), key=lambda i: i.order)
        b = self.beta(t)
        return ideas, [self.mu(ctx, i) + b * self.sigma(ctx, i) for i in ideas]

    def _scale(self, ctx: EvolveContext) -> float:
        """A reference spread for `norm="absolute"`: the range of measured code, floored."""
        vals = [s for s in (self._score(c) for c in ctx.store.of_kind(CODE)) if s is not None]
        return max(self.sigma_impl, (max(vals) - min(vals)) if len(vals) > 1 else 0.0)

    def select_probs(self, ctx: EvolveContext, t: float) -> Tuple[List[Individual], List[float]]:
        ideas, a = self.acquisition(ctx, t)
        if not ideas:
            return [], []
        if len(ideas) == 1:
            return ideas, [1.0]
        T = max(1e-6, self.temperature(t))
        if self.norm == "absolute":
            z = [(v - max(a)) / (self._scale(ctx) * T) for v in a]
        else:
            lo, hi = min(a), max(a)
            an = [(v - lo) / (hi - lo + 1e-9) for v in a]
            z = [(v - max(an)) / T for v in an]
        e = [math.exp(max(-700.0, v)) for v in z]
        tot = sum(e) or 1.0
        return ideas, [v / tot for v in e]

    def sample_idea(self, ctx: EvolveContext, t: float) -> Optional[Individual]:
        ideas, p = self.select_probs(ctx, t)
        if not ideas:
            return None
        r, acc = self.rng.random(), 0.0
        for ind, w in zip(ideas, p):
            acc += w
            if r <= acc:
                return ind
        return ideas[-1]

    # ---- lifecycle --------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        for s in seeds:
            if s.kind == CODE and self.seed_code is None:
                self.seed_code = s.id
                if self._score(s) is None:
                    # Worth saying loudly. Every base falls back to 0, so every measured gain is
                    # really an absolute score wearing a gain's name, and the judge calibrates
                    # against it. A seed that did not evaluate is a misconfigured run, not a hard
                    # problem, and it is cheaper to notice now than after the budget is spent.
                    logger.warning(
                        f"seed program has no usable {self.score_key!r}: base will be 0 for "
                        f"every idea and measured gains will not mean what they say. Check the "
                        f"evaluator -- its last measurement was "
                        f"{s.metrics() or 'empty'}"
                    )

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        t = self.progress(ctx)
        p_new, p_ref, p_impl = self.mix(t)
        items: List[Create] = []
        for _ in range(max(0, n)):
            has_ideas = bool(ctx.store.of_kind(IDEA))
            act = NEW if not has_ideas else self._sample_action(p_new, p_ref, p_impl)
            if act == NEW:
                items.append(self._new_idea(ctx, t, parent=None))
                continue
            idea = self.sample_idea(ctx, t)
            if idea is None:
                items.append(self._new_idea(ctx, t, parent=None))
            elif act == REFINE:
                items.append(self._new_idea(ctx, t, parent=idea))
            else:
                items.append(self._implement(ctx, t, idea))
        return items

    def _sample_action(self, p_new: float, p_ref: float, p_impl: float) -> str:
        r = self.rng.random()
        if r < p_new:
            return NEW
        if r < p_new + p_ref:
            return REFINE
        return IMPL

    def _new_idea(self, ctx: EvolveContext, t: float, parent: Optional[Individual]) -> Create:
        inherited, inherited_ind = self.pool_best(ctx, parent.id if parent else None)
        return Create(
            kind=IDEA,
            parent_ids=[parent.id] if parent else [],
            k=self.k_ideas,
            context=PromptContext(
                instruction=ctx.objective,
                parents=[parent] if parent else [],
                history=self._idea_history(ctx),
                extra={"t": round(t, 4), "action": REFINE if parent else NEW,
                       "inherited_score": inherited,
                       "inherited_code": (inherited_ind.id if inherited_ind else None)},
            ),
            meta={"t": round(t, 4), "action": REFINE if parent else NEW,
                  "base": inherited},
        )

    def _implement(self, ctx: EvolveContext, t: float, idea: Individual) -> Create:
        base, start = self.pool_best(ctx, idea.id)
        item = Create(
            kind=CODE,
            parent_ids=[start.id] if start else [],
            anchor_id=idea.id,
            k=self.k_code,
            context=PromptContext(
                instruction=self._code_instruction(ctx, idea),
                parents=[start] if start else [],
                history=self._code_history(ctx, idea.id),
                extra={"idea": idea.genome.render(), "t": round(t, 4), "base": base},
            ),
            meta={"t": round(t, 4), "action": IMPL, "idea": idea.id, "base": base},
        )
        # Pinned now, not recomputed later. C*(I) grows while this item is in flight, so a base
        # read back at measurement time would be a different number and every Delta computed from
        # it would be wrong.
        self.issued_base[item.batch_id] = base if base is not None else 0.0
        return item

    def _code_instruction(self, ctx: EvolveContext, idea: Individual) -> str:
        return (f"{ctx.objective}\n\n## The approach to implement\n{idea.genome.render()}\n\n"
                "Implement this approach. If the current program follows a different approach, "
                "replace it -- do not blend the two, and do not substitute an improvement you "
                "happen to prefer. Verify the program runs and satisfies the problem's "
                "constraints before submitting it.")

    def _idea_history(self, ctx: EvolveContext) -> str:
        rows = []
        ideas = sorted(ctx.store.of_kind(IDEA), key=lambda i: -self.mu(ctx, i))[:8]
        for i in ideas:
            own = self.own_scores(ctx, i.id)
            head = " ".join(i.genome.render().split())[:180]
            if own:
                rows.append(f"- reached {max(own):.4f} over {len(own)} implementation(s): {head}")
            else:
                rows.append(f"- never implemented (predicted {self.delta_hat(i):+.4f}): {head}")
        if not rows:
            return ""
        return ("\n".join(rows) + "\n\nAn approach that was implemented and scored badly is "
                "settled -- do not repropose it. An approach never implemented is untested, "
                "not refuted.")

    def _code_history(self, ctx: EvolveContext, idea_id: str) -> str:
        rows = []
        for c in sorted(ctx.store.anchored_on(idea_id), key=lambda x: x.order)[-6:]:
            s = self._score(c)
            rows.append(f"- #{c.order}: {self.score_key}="
                        f"{f'{s:.4f}' if s is not None else 'infeasible'} "
                        f"{c.meta.get('summary', '')}".rstrip())
        return "\n".join(rows)

    # ---- learning ---------------------------------------------------------

    async def on_measured(self, ctx: EvolveContext, ind: Individual,
                          m: Measurement) -> None:
        if ind.kind != CODE:
            return
        if "base" not in ind.meta:
            # The variator builds the child and never sees the work item's meta, so the base this
            # implementation actually started from is carried here, from what was pinned at issue.
            ind.meta["base"] = self.issued_base.get(m.batch_id, 0.0)
        idea_id = ind.anchor_id
        if not idea_id or idea_id in self.trained:
            return
        score = self._score(ind)
        if score is None:
            return                          # infeasible: measures the generator, not the idea
        idea = ctx.store.get(idea_id)
        if idea is None or self.judge is None:
            return
        base = float(ind.meta.get("base") or 0.0)
        raw = None
        for mm in idea.measurements:
            v = mm.metrics.get("idea_raw")
            if isinstance(v, (int, float)):
                raw = float(v)
                break
        if raw is None:
            return                          # never judged; nothing to calibrate against
        self.trained.append(idea_id)
        gain = score - base
        self.judge.observe(idea.genome.render(), raw, base, gain)
        self.history.append({"idea": idea_id, "raw": raw, "base": base, "gain": gain,
                             "score": score, "t": round(self.progress(ctx), 4)})
        if self.refit_every and len(self.trained) % self.refit_every == 0:
            diag = self.judge.fit()
            logger.info(f"[judge] {diag}")

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        return None

    # ---- reporting --------------------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = CODE) -> Ranking:
        if kind == IDEA:
            return Ranking.by_score({i.id: self.mu(ctx, i) for i in ctx.store.of_kind(IDEA)})
        scores = {}
        for c in ctx.store.of_kind(CODE):
            s = self._score(c)
            if s is not None:
                scores[c.id] = s
        return Ranking.by_score(scores)

    def prior_vs_realised(self, ctx: EvolveContext) -> List[Dict[str, Any]]:
        """What the judge predicted against what its implementations achieved.

        The point of recording both is that the method can be asked whether its judge is worth
        having, instead of the question being unanswerable.
        """
        out = []
        for i in sorted(ctx.store.of_kind(IDEA), key=lambda x: x.order):
            own = self.own_scores(ctx, i.id)
            if not own:
                continue
            code = [c for c in ctx.store.anchored_on(i.id) if self._score(c) is not None]
            first = min(code, key=lambda c: c.order) if code else None
            base = float(first.meta.get("base") or 0.0) if first is not None else 0.0
            out.append({
                "idea": i.id, "order": i.order,
                "predicted": self.delta_hat(i),
                "realised_first": (self._score(first) - base) if first is not None else None,
                "realised_best": max(own) - base,
                "implementations": len(own),
                "summary": " ".join(i.genome.render().split())[:120],
            })
        return out

    # ---- operator ---------------------------------------------------------

    def default_variator(self, *, evaluator=None, model: str = "high", timeout: float = 1800,
                         target_file: Optional[str] = None, sandbox: bool = False, **kw):
        """Prose for ideas, a coding agent for implementations.

        The agent is chosen because it can read an error and try again, which a single completion
        cannot. It is **not** chosen to keep infeasible programs out: measured side by side on this
        problem the agent submitted infeasible programs at 13.3% against the blind completion's
        7.3%, so if anything it is worse at that. Feasibility is handled by `_score` refusing to
        read a violated constraint as a number, and that is where it belongs -- a rule the method
        enforces rather than a behaviour it hopes the operator has.
        """
        from ..variators.idea import IdeaCodeVariator, IdeaVariator

        idea = IdeaVariator(model=model, timeout=min(timeout, 300.0))
        code_system = (
            "You are an expert algorithm designer implementing a specified approach. Implement "
            "the approach you were given -- not a different improvement you happen to prefer."
        )
        if evaluator is None:
            from ..variators.completion import CompletionVariator

            logger.warning(
                "annealed_idea_code has no evaluator to give the coding agent, so it falls back "
                "to a blind completion. Implementation noise will dominate the judge's training "
                "signal; pass evaluator= for the operator this method is defined with."
            )
            return IdeaCodeVariator(idea_variator=idea, code_variator=CompletionVariator(
                model=model, timeout=timeout, target_file=target_file,
                score_key=self.score_key, system_prompt=code_system))
        if sandbox:
            from ..variators.sandbox import SandboxVariator

            return IdeaCodeVariator(idea_variator=idea, code_variator=SandboxVariator(
                evaluator_code=kw.get("evaluator_code", ""), model=model,
                timeout=int(timeout)))
        from ..variators.agent import AgentVariator

        # Every operator knob the caller passed has to be forwarded, not the subset this method
        # happens to think about. Dropping `max_tool_calls` here gave this method's agent an
        # unlimited action budget while every other arm ran on 14, and the comparison that came out
        # of it read as a policy result when it was a budget result.
        return IdeaCodeVariator(idea_variator=idea, code_variator=AgentVariator(
            evaluator=evaluator, model=model, timeout=timeout, score_key=self.score_key,
            max_evaluations=kw.get("max_evaluations"),
            max_tool_calls=kw.get("max_tool_calls"),
            max_turns=kw.get("max_turns"),
            warm_start_file=kw.get("warm_start_file"),
            workspace_root=kw.get("workspace_root"),
            instruction_suffix=kw.get("instruction_suffix", "")))

    # ---- persistence ------------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {
            "seed_code": self.seed_code,
            "issued_base": dict(self.issued_base),
            "trained": list(self.trained),
            "history": list(self.history),
            "rng": list(self.rng.getstate()[1]),
            "judge": self.judge.state_dict() if self.judge is not None else None,
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.seed_code = state.get("seed_code")
        self.issued_base = {str(k): float(v) for k, v in (state.get("issued_base") or {}).items()}
        self.trained = list(state.get("trained", []))
        self.history = list(state.get("history", []))
        st = state.get("rng")
        if st:
            try:
                self.rng.setstate((3, tuple(int(v) for v in st), None))
            except (TypeError, ValueError):
                pass
        if self.judge is not None and state.get("judge"):
            self.judge.load_state_dict(state["judge"])

    def reconcile(self, ctx: EvolveContext) -> None:
        if self.seed_code and self.seed_code in ctx.store:
            pass
        else:
            roots = [i for i in sorted(ctx.store, key=lambda x: x.order)
                     if i.kind == CODE and not i.parent_ids]
            self.seed_code = roots[0].id if roots else None
        self.trained = [i for i in self.trained if i in ctx.store]

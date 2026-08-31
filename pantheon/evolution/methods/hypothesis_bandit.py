"""HypothesisBandit -- hypothesis-guided adaptive co-evolution (the pantheon_evolve_2 core).

Named for the two mechanisms that actually decide: HYPOTHESES are the search representation,
and a BANDIT over their measured evidence allocates the budget.

The design document ("A Technical Revision Strategy...", pantheon_evolve_2.pdf) prescribes five
mechanisms; this implements the four that have a substrate on code benchmarks, in the shape its
p.13 "immediate priority" asks for:

  1. **A structured hypothesis archive.** Two coupled populations: hypotheses H (kind="idea",
     structured text: target component, mechanism, expected effect, falsification test) and
     programs P (kind="code"). h -> p -> E(p) -> D(p) -> h': implementations carry their
     hypothesis as `anchor_id`, measurements feed back into per-hypothesis evidence, and
     diagnostics (rule-based, from the evaluator's own feedback) flow into later proposals.
  2. **Component-aware credit assignment**, the paper's "practical version" minus automated
     reversion: every mutation targets ONE named component of a fixed vocabulary, the changed
     component is recorded on the child, and A(v) = E[dR | v changed] - E[dR | v unchanged] is
     estimated observationally. Credits steer which component the next hypothesis targets.
  3. **Adaptive branch allocation.** priority(h) = mu_h + lambda*sigma_h + eta*novelty_h, a
     softmax-sampled bandit over LIVE hypotheses; hypotheses whose evidence turns negative are
     retired. mu is measured evidence only -- there is no LLM judge, deliberately: the AHC039
     E1 ablation showed a judge's opinion inverting against random on exactly this kind of
     search, and the paper's controller is a bandit on measurements anyway.
  4. **Multi-fidelity staging.** On tasks that expose a cheap fidelity, implementations are
     measured at `low` first and promoted to a full measurement only if the cheap reading does
     not fall below the incumbent by more than a margin. Cheap readings never become the
     recorded score (rank uses full-fidelity measurements only).

Out of scope here, documented rather than pretended: stress/transfer/red-team evaluators (the
paper's mechanism 4) need a family of related datasets, which Erdos / circle packing / AHC039
do not have; trajectory memory beyond this run (mechanism 5) is serialized in `state_dict` but
no cross-task retrieval is implemented.

One more lesson is baked in: the implement step is EDIT-shaped. The AHC039 campaign showed
"implement this approach, replacing the program" wrecks a mature incumbent in 84% of attempts;
here the agent is told to change only what the hypothesis targets and keep the rest intact.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pantheon.utils.log import logger

from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext, Remeasure

HYP, CODE = "idea", "code"

COMPONENTS = [
    "initialization",
    "core-algorithm",
    "search-strategy",
    "numerical-optimization",
    "parameters",
    "output-construction",
]
"""The component vocabulary mutations target. Deliberately generic: the point of credit
assignment is to learn, per task, which of these is worth touching -- not to hand-craft a
per-task ontology."""

HYP_FORMAT = (
    "Reply with EXACTLY this structure (keep the field names):\n"
    "target_component: <one of: {components}>\n"
    "mechanism: <the specific change, one or two sentences>\n"
    "expected_effect: <what should improve, and why>\n"
    "falsification: <what result would show this hypothesis is wrong>\n"
)


class HypothesisGate:
    """The evaluator for kind="idea": structural acceptance, no opinion.

    A hypothesis is admitted if it names a known component and a mechanism -- nothing scores
    it. Its value is established by its implementations' measurements and nothing else (the
    judge ablation is why). Registered by the method through `default_evaluators`.
    """

    kind = HYP

    async def measure(self, ctx: EvolveContext, ind: Individual,
                      fidelity: str = "full") -> Measurement:
        text = ind.genome.render()
        comp = _parse_component(text)
        ok = comp is not None and "mechanism" in text.lower()
        return Measurement(individual_id=ind.id, ok=True,
                           metrics={"validity": 1.0 if ok else 0.0,
                                    "structured": 1.0 if ok else 0.0},
                           artifacts={} if ok else {"error": "unstructured hypothesis"},
                           fidelity=fidelity, cost=0.0)


def _parse_component(text: str) -> Optional[str]:
    low = text.lower()
    for line in low.splitlines():
        if line.strip().startswith("target_component"):
            for c in COMPONENTS:
                if c in line:
                    return c
    # fall back to any mention, first match wins
    for c in COMPONENTS:
        if c in low:
            return c
    return None


class HypothesisBandit(BaseMethod):
    """Hypothesis-guided adaptive co-evolution."""

    name = "hypothesis_bandit"

    def __init__(
        self,
        *,
        lam: float = 1.0,
        eta: float = 0.5,
        tau: float = 0.35,
        prior_sigma: float = 0.05,
        min_live_hyps: int = 3,
        max_live_hyps: int = 8,
        retire_after: int = 2,
        refine_after: int = 2,
        low_fidelity: bool = False,
        promote_margin: float = 0.02,
        score_key: str = "combined_score",
        valid_key: str = "validity",
        k_hyps: int = 1,
        k_code: int = 1,
        seed: int = 0,
    ):
        self.lam, self.eta, self.tau = lam, eta, tau
        self.prior_sigma = prior_sigma
        self.min_live, self.max_live = min_live_hyps, max_live_hyps
        self.retire_after = retire_after
        self.refine_after = refine_after
        self.low_fidelity = low_fidelity
        self.promote_margin = promote_margin
        self.score_key, self.valid_key = score_key, valid_key
        self.k_hyps, self.k_code = k_hyps, k_code
        self.rng = random.Random(seed)

        self.hyp: Dict[str, Dict[str, Any]] = {}
        """id -> {component, gains: [dR], fails: int, retired: bool, refined_from: id|None}"""
        self.credit: Dict[str, List[float]] = {c: [] for c in COMPONENTS}
        """component -> observed dR whenever a mutation touched it (the A(v) estimate's
        'changed' arm; the 'unchanged' arm is every other entry)."""
        self.events: List[Dict[str, Any]] = []
        """the trajectory log z_t -- serialized for future cross-task memory."""
        self.issued_base: Dict[str, float] = {}
        self.issued_comp: Dict[str, str] = {}
        self.pending_promote: List[Remeasure] = []
        self.seed_code: Optional[str] = None

    # ---- reading the store -------------------------------------------------

    def _score(self, ind: Optional[Individual], full_only: bool = True) -> Optional[float]:
        """Feasible score of a program; infeasible is None, never zero (annealed's lesson).
        Cheap-fidelity readings never become the recorded score."""
        if ind is None:
            return None
        for m in reversed(ind.measurements):
            if not m.ok:
                continue
            if full_only and m.fidelity != "full":
                continue
            if m.metrics.get(self.valid_key, 1.0) <= 0:
                return None
            v = m.metrics.get(self.score_key)
            if isinstance(v, (int, float)):
                return float(v)
        return None

    def best_code(self, ctx: EvolveContext) -> Tuple[Optional[float], Optional[Individual]]:
        best_s, best_i = None, None
        for c in ctx.store.of_kind(CODE):
            s = self._score(c)
            if s is not None and (best_s is None or s > best_s):
                best_s, best_i = s, c
        return best_s, best_i

    # ---- the controller ----------------------------------------------------

    def _mu_sigma_nov(self, h: Dict[str, Any]) -> Tuple[float, float, float]:
        n = len(h["gains"])
        mu = sum(h["gains"]) / n if n else 0.0
        sigma = self.prior_sigma / math.sqrt(1 + n)
        nov = 1.0 / (1.0 + n)
        return mu, sigma, nov

    def live_hyps(self) -> List[str]:
        return [i for i, h in self.hyp.items() if not h["retired"]]

    def sample_hyp(self) -> Optional[str]:
        ids = self.live_hyps()
        if not ids:
            return None
        pr = []
        for i in ids:
            mu, sg, nov = self._mu_sigma_nov(self.hyp[i])
            pr.append(mu + self.lam * sg + self.eta * nov)
        m = max(pr)
        e = [math.exp((v - m) / max(self.tau, 1e-6)) for v in pr]
        tot = sum(e)
        r, acc = self.rng.random() * tot, 0.0
        for i, w in zip(ids, e):
            acc += w
            if r <= acc:
                return i
        return ids[-1]

    def component_headroom(self) -> str:
        """The component the next hypothesis should target: highest optimistic credit --
        A(v) plus an uncertainty bonus, so unexplored components get their turn."""
        all_gains = [g for gs in self.credit.values() for g in gs]
        overall = sum(all_gains) / len(all_gains) if all_gains else 0.0
        best_c, best_v = COMPONENTS[0], -1e9
        for c in COMPONENTS:
            gs = self.credit[c]
            a = (sum(gs) / len(gs) - overall) if gs else 0.0
            bonus = self.prior_sigma / math.sqrt(1 + len(gs))
            v = a + self.lam * bonus + self.rng.random() * 1e-6
            if v > best_v:
                best_c, best_v = c, v
        return best_c

    def credit_table(self) -> str:
        rows = []
        for c in COMPONENTS:
            gs = self.credit[c]
            if gs:
                rows.append(f"- {c}: {len(gs)} edits, mean dR {sum(gs)/len(gs):+.4f}")
            else:
                rows.append(f"- {c}: unexplored")
        return "\n".join(rows)

    # ---- lifecycle ---------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        for s in seeds:
            if s.kind == CODE and self.seed_code is None:
                self.seed_code = s.id

    async def ask(self, ctx: EvolveContext, n: int) -> List[Any]:
        items: List[Any] = []
        while self.pending_promote and len(items) < n:
            items.append(self.pending_promote.pop(0))
        while len(items) < n:
            live = self.live_hyps()
            evidenced = [i for i in live if self.hyp[i]["gains"]]
            if len(live) < self.min_live:
                items.append(self._propose(ctx))
                continue
            # refine a working hypothesis occasionally: evidence-rich and positive
            refinable = [i for i in evidenced
                         if len(self.hyp[i]["gains"]) >= self.refine_after
                         and sum(self.hyp[i]["gains"]) > 0]
            if refinable and len(live) < self.max_live and self.rng.random() < 0.2:
                items.append(self._propose(ctx, refine=self.rng.choice(refinable)))
                continue
            h = self.sample_hyp()
            if h is None:
                items.append(self._propose(ctx))
            else:
                items.append(self._implement(ctx, h))
        return items

    # ---- item builders -----------------------------------------------------

    def _recent_failures(self) -> str:
        rows = [e for e in self.events if e.get("kind") == "fail"][-5:]
        return "\n".join(f"- {r['reason'][:140]}" for r in rows)

    def _hyp_list(self) -> str:
        rows = []
        for i, h in self.hyp.items():
            if h["retired"]:
                continue
            mu, _, _ = self._mu_sigma_nov(h)
            state = (f"{len(h['gains'])} impls, mean dR {mu:+.4f}" if h["gains"]
                     else "untested")
            rows.append(f"- [{h['component']}] ({state}) {h['head'][:150]}")
        return "\n".join(rows)

    def _propose(self, ctx: EvolveContext, refine: Optional[str] = None) -> Create:
        target = self.hyp[refine]["component"] if refine else self.component_headroom()
        best_s, _ = self.best_code(ctx)
        parts = [ctx.objective,
                 f"\n## Current best score\n{best_s if best_s is not None else 'none yet'}",
                 "\n## Component credit so far (dR = change in score when an edit touched "
                 "the component)\n" + self.credit_table()]
        live = self._hyp_list()
        if live:
            parts.append("\n## Hypotheses already in play (do not repeat them)\n" + live)
        fails = self._recent_failures()
        if fails:
            parts.append("\n## Recent failure diagnostics\n" + fails)
        if refine:
            parts.append("\n## Refine THIS hypothesis using its evidence\n"
                         + self.hyp[refine]["text"])
            parts.append("Sharpen the mechanism: keep what the evidence supports, change what "
                         "it does not.")
        else:
            parts.append(f"\nPropose ONE new hypothesis targeting the component "
                         f"`{target}` of the current program.")
        parts.append("\n" + HYP_FORMAT.format(components=", ".join(COMPONENTS)))
        return Create(
            kind=HYP,
            parent_ids=[refine] if refine else [],
            k=self.k_hyps,
            context=PromptContext(instruction="\n".join(parts)),
            meta={"op": "refine" if refine else "propose", "target": target},
        )

    def _implement(self, ctx: EvolveContext, hyp_id: str) -> Create:
        h = self.hyp[hyp_id]
        base, start = self.best_code(ctx)
        instruction = (
            f"{ctx.objective}\n\n"
            f"## The hypothesis to test (target component: {h['component']})\n{h['text']}\n\n"
            "EDIT the current program to test this hypothesis: change ONLY what the hypothesis "
            "targets and keep everything else intact -- do not restructure or rewrite parts the "
            "hypothesis does not touch. Verify with the evaluator before submitting. If the "
            "edit cannot beat the current program, still submit your best verified attempt."
        )
        fidelity = "low" if self.low_fidelity else "full"
        item = Create(
            kind=CODE,
            parent_ids=[start.id] if start else [],
            anchor_id=hyp_id,
            k=self.k_code,
            fidelity=fidelity,
            context=PromptContext(
                instruction=instruction,
                parents=[start] if start else [],
                extra={"component": h["component"]},
            ),
            meta={"op": "implement", "hyp": hyp_id, "component": h["component"],
                  "base": base},
        )
        # Pinned at issue time: the variator builds the child without seeing the work item's
        # meta, so both the base and the targeted component are carried to `on_measured` here.
        self.issued_base[item.batch_id] = base if base is not None else 0.0
        self.issued_comp[item.batch_id] = h["component"]
        return item

    # ---- learning ----------------------------------------------------------

    async def on_measured(self, ctx: EvolveContext, ind: Individual,
                          m: Measurement) -> None:
        if ind.kind == HYP:
            if m.metrics.get("structured", 1.0) <= 0:
                # unparseable hypothesis: dead on arrival, never selectable
                self.hyp[ind.id] = {"component": COMPONENTS[0], "text": ind.genome.render(),
                                    "head": "", "gains": [], "fails": 0, "retired": True}
                return
            text = ind.genome.render()
            self.hyp[ind.id] = {
                "component": _parse_component(text) or COMPONENTS[0],
                "text": text,
                "head": " ".join(text.split()),
                "gains": [], "fails": 0, "retired": False,
            }
            self.events.append({"kind": "hyp", "id": ind.id})
            return

        if ind.kind != CODE:
            return
        if "base" in ind.meta:
            base = float(ind.meta["base"] or 0.0)
        else:
            base = self.issued_base.get(m.batch_id, 0.0)
            ind.meta["base"] = base
        comp = ind.meta.get("component") or self.issued_comp.get(m.batch_id)
        if comp and "component" not in ind.meta:
            ind.meta["component"] = comp
        hyp_id = ind.anchor_id

        # staged fidelity: a cheap reading either earns a full measurement or ends the line
        if m.fidelity != "full":
            v = m.metrics.get(self.score_key)
            feasible = m.metrics.get(self.valid_key, 1.0) > 0 and isinstance(v, (int, float))
            if feasible and float(v) >= base - self.promote_margin:
                self.pending_promote.append(Remeasure(individual_id=ind.id, fidelity="full"))
            else:
                self.events.append({"kind": "fail", "reason":
                                    f"low-fidelity screen: {v} vs base {base:.4f}",
                                    "component": comp})
                if hyp_id in self.hyp:
                    self.hyp[hyp_id]["fails"] += 1
                    self._maybe_retire(hyp_id)
            return

        score = self._score(ind)
        if score is None:
            reason = str(m.artifacts.get("error", m.metrics.get("invalid_reason", "infeasible")))
            self.events.append({"kind": "fail", "reason": reason, "component": comp})
            if hyp_id in self.hyp:
                self.hyp[hyp_id]["fails"] += 1
                self._maybe_retire(hyp_id)
            return

        d = score - base
        if comp in self.credit:
            self.credit[comp].append(d)
        if hyp_id in self.hyp:
            self.hyp[hyp_id]["gains"].append(d)
            self._maybe_retire(hyp_id)
        self.events.append({"kind": "measure", "id": ind.id, "hyp": hyp_id,
                            "component": comp, "dR": d, "score": score})

    def _maybe_retire(self, hyp_id: str) -> None:
        h = self.hyp[hyp_id]
        n = len(h["gains"])
        if n >= self.retire_after and sum(h["gains"]) / n <= 0:
            h["retired"] = True
        if h["fails"] >= 2 and n == 0:
            h["retired"] = True

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        self.events.append({"kind": "fail", "reason": f"{f.stage}: {f.reason}"})

    # ---- reporting / persistence -------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = CODE) -> Ranking:
        if kind == HYP:
            scores = {}
            for i, h in self.hyp.items():
                mu, _, _ = self._mu_sigma_nov(h)
                scores[i] = mu
            return Ranking.by_score(scores)
        return Ranking.by_score({c.id: s for c in ctx.store.of_kind(CODE)
                                 if (s := self._score(c)) is not None})

    def state_dict(self) -> Dict[str, Any]:
        return {"hyp": self.hyp, "credit": self.credit, "events": self.events[-500:],
                "seed_code": self.seed_code, "rng": self.rng.getstate()}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.hyp = dict(state.get("hyp", {}))
        self.credit = {c: list(state.get("credit", {}).get(c, [])) for c in COMPONENTS}
        self.events = list(state.get("events", []))
        self.seed_code = state.get("seed_code")
        rng = state.get("rng")
        if rng:
            try:
                self.rng.setstate(tuple(rng) if isinstance(rng, list) else rng)
            except (TypeError, ValueError):
                pass

    def reconcile(self, ctx: EvolveContext) -> None:
        for i in ctx.store.of_kind(HYP):
            if i.id not in self.hyp:
                text = i.genome.render()
                self.hyp[i.id] = {"component": _parse_component(text) or COMPONENTS[0],
                                  "text": text, "head": " ".join(text.split()),
                                  "gains": [], "fails": 0, "retired": False}
        if self.seed_code is None:
            for c in sorted(ctx.store.of_kind(CODE), key=lambda x: x.order):
                if not c.parent_ids:
                    self.seed_code = c.id
                    break

    # ---- the components it brings ------------------------------------------

    def default_evaluators(self, **kw) -> Dict[str, Any]:
        """The structural gate for hypotheses. `code` stays the problem's, as always."""
        return {HYP: HypothesisGate()}

    def default_variator(self, *, evaluator=None, model: str = "high", timeout: float = 1800,
                         target_file: Optional[str] = None, sandbox: bool = False, **kw):
        """Prose for hypotheses, an EDIT-shaped coding agent for implementations."""
        from ..variators.idea import IdeaCodeVariator, IdeaVariator

        idea = IdeaVariator(model=model, timeout=min(timeout, 300.0))
        code_system = (
            "You are an expert algorithm engineer testing a specific hypothesis by EDITING an "
            "existing program. Change only what the hypothesis targets; keep the rest of the "
            "program intact. Verify your edit with the evaluator before submitting."
        )
        if evaluator is None:
            from ..variators.completion import CompletionVariator

            logger.warning(
                "hypothesis_bandit has no evaluator to give the coding agent; falling back to a "
                "blind completion. Pass evaluator= for the operator this method is defined with."
            )
            code = CompletionVariator(model=model, timeout=timeout, target_file=target_file,
                                      system_prompt=code_system)
        else:
            from ..variators.agent import AgentVariator

            code = AgentVariator(evaluator=evaluator, model=model, timeout=timeout,
                                 system_prompt=code_system, score_key=self.score_key,
                                 workspace_root=kw.get("workspace_root"),
                                 max_tool_calls=kw.get("max_tool_calls", 28),
                                 max_evaluations=kw.get("max_evaluations"),
                                 inner_fidelity=kw.get("inner_fidelity", "full"),
                                 trace_path=kw.get("trace_path"))
        return IdeaCodeVariator(idea_variator=idea, code_variator=code)

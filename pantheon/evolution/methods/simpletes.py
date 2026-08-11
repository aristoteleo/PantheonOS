"""SimpleTES, expressed against the method interface.

This exists to test the abstraction, not to vendor someone else's system. SimpleTES
(github.com/wq-will/SimpleTES) is an independently designed search: C parallel chains, each a DAG
of attempts; every step samples a set of nodes from one chain, asks a model for K candidates from
one prompt, evaluates them in isolation, and commits only the best to that chain. Nothing about it
resembles MAP-Elites -- no bins, no islands, no elite grid -- so if it can be written here without
touching the framework, the seam is in the right place.

Two things it forced into the interface, both of which were missing from the first sketch:

  - `ask` returns a batch and `on_measured` fires per child, because the K candidates of one batch
    come back one at a time and the best-of-K commit can only happen once all K have resolved
  - `on_failed` is mandatory, because a model returning fewer than K candidates is routine and the
    batch would otherwise never complete

The selector is a separate object for the same reason it is in the original: it is the axis they
vary to get six algorithms, and it is the part someone will want to replace.

**Read against the upstream source, and corrected.** The first version of this port invented a
distinction that is not there: it treated the first selected node as THE parent and the rest as
"inspirations". Upstream, the selected set is the parent set --

    parent_ids=list(task.inspiration_ids)                        engine/runtime.py
    def on_child_done(self, child, parents):  # "parents: Parent nodes (inspirations)"

-- and the generation prompt has no notion of a current program at all. It shows the immutable
scaffold, then `[SAMPLED INSPIRATIONS] (n solutions sampled for detailed reference)`, and asks for
a new block: *"Prioritize NOVEL approaches ... Combine insights from multiple solutions"*. So the
lineage is a genuine multi-parent DAG, which is also why their value backpropagation is described
as DAG-aware rather than tree-aware.

Still not ported, deliberately: `reflection_mode` (an extra model call summarising each winner) and
the `llm_elite` / `llm_refine_*` selectors (a second LLM pass that re-ranks a shortlist). Both add
model calls to the SELECTION step, which is a different kind of algorithm from the three here, and
neither is needed to test the seam.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext


def score_of(ind: Individual, key: str) -> Optional[float]:
    m = ind.measured()
    if m is None:
        return None
    v = m.metrics.get(key)
    return float(v) if isinstance(v, (int, float)) else None


class Selector:
    """History -> which nodes to build the next prompt on. SimpleTES's phi lever.

    `chain` arrives sorted by score, best first, which every selector here relies on.
    """

    name = "balance"

    def __init__(self, exploitation_ratio: float = 0.7, exploration_ratio: float = 0.2,
                 elite_ratio: float = 0.2):
        self.exploitation_ratio = exploitation_ratio
        self.exploration_ratio = exploration_ratio
        self.elite_ratio = elite_ratio

    def pick(self, chain: List[Individual], n: int, rng: random.Random,
             key: str) -> List[Individual]:
        """The best node, then a stratified draw over the rest.

        Three tiers rather than a shuffle: most draws come from the elite head, some from the
        middle of the ranking, and a few from anywhere. A plain shuffle over "everything except
        the best" makes the middle and the tail equally likely, which is a different search.
        """
        if not chain or n <= 0:
            return []
        if len(chain) <= n:
            return list(chain)

        result = [chain[0]]
        used = {chain[0].id}
        size = len(chain)
        elite_end = max(1, int(size * self.elite_ratio))
        mid_start = max(1, int(size * 0.1))
        mid_end = max(2, int(size * 0.6))

        for _ in range(n - 1):
            roll = rng.random()
            if roll < self.exploitation_ratio:
                pool = chain[:elite_end]
            elif roll < self.exploitation_ratio + self.exploration_ratio:
                pool = chain[mid_start:mid_end] if mid_end > mid_start else chain
            else:
                pool = chain
            avail = [c for c in pool if c.id not in used] or [c for c in chain if c.id not in used]
            if not avail:
                break
            pick = rng.choice(avail)
            result.append(pick)
            used.add(pick.id)
        return result


class PUCTSelector(Selector):
    """Tree-search scoring: exploit the node's own value, explore by visit count."""

    name = "puct"

    def __init__(self, c_puct: float = 1.4, **kw):
        super().__init__(**kw)
        self.c_puct = c_puct
        self.visits: Dict[str, int] = {}

    def pick(self, chain, n, rng, key):
        if not chain:
            return []
        total = max(1, sum(self.visits.get(c.id, 0) for c in chain))

        def u(c: Individual) -> float:
            q = score_of(c, key) or 0.0
            nvis = self.visits.get(c.id, 0)
            return q + self.c_puct * math.sqrt(math.log(total + 1) / (1 + nvis))

        chosen = sorted(chain, key=u, reverse=True)[:n]
        for c in chosen:
            self.visits[c.id] = self.visits.get(c.id, 0) + 1
        return chosen


class RPUCGSelector(PUCTSelector):
    """DAG-aware with gamma decay: a node's value is discounted by depth, so long unproductive
    lines lose out to shallower ones with the same score."""

    name = "rpucg"

    def __init__(self, c_puct: float = 1.4, gamma: float = 0.8, **kw):
        super().__init__(c_puct, **kw)
        self.gamma = gamma
        self.depth: Dict[str, int] = {}

    def pick(self, chain, n, rng, key):
        if not chain:
            return []
        total = max(1, sum(self.visits.get(c.id, 0) for c in chain))

        def u(c: Individual) -> float:
            q = (score_of(c, key) or 0.0) * (self.gamma ** self.depth.get(c.id, 0))
            nvis = self.visits.get(c.id, 0)
            return q + self.c_puct * math.sqrt(math.log(total + 1) / (1 + nvis))

        chosen = sorted(chain, key=u, reverse=True)[:n]
        for c in chosen:
            self.visits[c.id] = self.visits.get(c.id, 0) + 1
        return chosen


SELECTORS = {s.name: s for s in (Selector, PUCTSelector, RPUCGSelector)}


@dataclass
class _Batch:
    """One prompt's worth of candidates, open until all k have resolved."""

    chain_idx: int
    parent_ids: List[str]
    k: int
    done: int = 0
    children: List[str] = field(default_factory=list)


class SimpleTES(BaseMethod):
    """C chains x K candidates per step, commit best-of-K to the chain that produced them."""

    name = "simpletes"

    def __init__(
        self,
        num_chains: int = 4,
        k_candidates: int = 4,
        num_inspirations: int = 5,
        min_inspirations: Optional[int] = None,
        max_inspirations: Optional[int] = None,
        selector: str = "balance",
        score_key: str = "combined_score",
        kind: str = "code",
        max_generations: Optional[int] = None,
        seed: int = 0,
    ):
        self.num_chains = num_chains
        self.k = k_candidates
        self.num_inspirations = num_inspirations
        self.min_inspirations = min_inspirations
        self.max_inspirations = max_inspirations
        """Upstream samples the count per batch when both are set, so successive prompts see
        different amounts of history. Fixed at `num_inspirations` when they are not."""
        self.score_key = score_key
        self.kind = kind
        sel = SELECTORS.get(selector, Selector)
        self.selector: Selector = sel() if isinstance(sel, type) else sel
        self.rng = random.Random(seed)
        self.chains: List[List[str]] = [[] for _ in range(num_chains)]
        self.open: Dict[str, _Batch] = {}
        self.failures: List[Dict[str, float]] = [{} for _ in range(num_chains)]
        """Per chain, not global. A pattern that keeps breaking one line of attack is not
        evidence about a different one, and pooling them puts noise in every prompt."""
        self.prompt_budget: Dict[int, int] = {}
        self.prompt_count: Dict[int, int] = {i: 0 for i in range(num_chains)}
        self.max_generations = max_generations
        self._next_chain = 0

    # ---- lifecycle -------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        for c in range(self.num_chains):
            self.chains[c] = [s.id for s in seeds]
        total = self.max_generations or (ctx.budget.max_items or 0)
        if total:
            # Chains get an equal share of the run and retire when it is spent, so a chain that
            # happens to be scheduled often cannot quietly consume the whole budget.
            #
            # The share is counted in PROMPTS. Upstream divides a generation budget by k because
            # its budget counts children; here one work item already is one prompt, so dividing
            # again would retire every chain after a single batch. And the remainder is handed
            # out rather than dropped -- with integer division the shares sum to less than the
            # budget, the leftover items belong to no chain, and the run stops early with money
            # unspent while claiming the budget stopped it.
            base, extra = divmod(total, self.num_chains)
            self.prompt_budget = {i: max(1, base + (1 if i < extra else 0))
                                  for i in range(self.num_chains)}

    def _chain_nodes(self, ctx: EvolveContext, c: int) -> List[Individual]:
        """The chain's nodes, best first. Every selector reads position 0 as the incumbent."""
        nodes = [ctx.store.get(i) for i in self.chains[c]]
        nodes = [n for n in nodes if n is not None]
        return sorted(nodes, key=lambda n: score_of(n, self.score_key) or -math.inf, reverse=True)

    def _inspiration_count(self, chain_len: int) -> int:
        n = min(self.num_inspirations, chain_len)
        if self.min_inspirations is None or self.max_inspirations is None:
            return n
        lo, hi = int(self.min_inspirations), int(self.max_inspirations)
        return max(1, min(chain_len, self.rng.randint(min(lo, hi), max(lo, hi))))

    def _retired(self, c: int) -> bool:
        cap = self.prompt_budget.get(c)
        return cap is not None and self.prompt_count.get(c, 0) >= cap

    # ---- the loop --------------------------------------------------------

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        """One batch per chain, at most one open batch per chain at a time.

        The per-chain limit is the backpressure: a chain whose batch is still evaluating has
        nothing meaningful to condition on yet, so asking it for more work would just build on
        stale history.
        """
        busy = {b.chain_idx for b in self.open.values()}
        items: List[Create] = []
        for _ in range(self.num_chains):
            if len(items) >= n:
                break
            c = self._next_chain
            self._next_chain = (self._next_chain + 1) % self.num_chains
            if c in busy or self._retired(c):
                continue
            nodes = self._chain_nodes(ctx, c)
            if not nodes:
                continue
            picked = self.selector.pick(nodes, self._inspiration_count(len(nodes)),
                                        self.rng, self.score_key)
            if not picked:
                continue
            # Every selected node is a parent. Upstream sets `parent_ids = inspiration_ids` and
            # its prompt shows them as peer references rather than one base plus decoration --
            # which is what makes the lineage a multi-parent DAG.
            item = Create(
                kind=self.kind,
                parent_ids=[p.id for p in picked],
                k=self.k,
                context=PromptContext(
                    instruction=ctx.objective,
                    parents=list(picked),
                    history=self._history_text(ctx, c),
                    failures=dict(self.failures[c]),
                ),
                meta={"chain": c, "selector": self.selector.name},
            )
            self.open[item.batch_id] = _Batch(chain_idx=c,
                                              parent_ids=[p.id for p in picked], k=self.k)
            self.prompt_count[c] = self.prompt_count.get(c, 0) + 1
            items.append(item)
            busy.add(c)
        return items

    def _history_text(self, ctx: EvolveContext, c: int) -> str:
        nodes = self._chain_nodes(ctx, c)[:6]
        lines = []
        for nd in nodes:
            s = score_of(nd, self.score_key)
            lines.append(f"- {nd.id}: {self.score_key}={s:.4f}" if s is not None
                         else f"- {nd.id}: (unscored)")
        return "\n".join(lines)

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        b = self.open.get(m.batch_id)
        if b is None:
            return
        b.children.append(ind.id)
        b.done += 1
        if b.done >= b.k:
            self._commit(ctx, m.batch_id)

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        b = self.open.get(f.batch_id)
        if b is None:
            return
        b.done += 1
        if f.reason:
            book = self.failures[b.chain_idx]
            book[f.reason] = book.get(f.reason, 0.0) + 1.0
        if b.done >= b.k:
            self._commit(ctx, f.batch_id)

    def _commit(self, ctx: EvolveContext, batch_id: str) -> None:
        """Best-of-K: exactly one child joins the chain; the rest stay in the store, unattached.

        They are not deleted -- they were measured, they cost money, and a later analysis may want
        them -- but they are not history the next prompt will see.
        """
        b = self.open.pop(batch_id, None)
        if b is None:
            return
        scored = []
        for cid in b.children:
            ind = ctx.store.get(cid)
            s = score_of(ind, self.score_key) if ind else None
            if s is not None:
                scored.append((s, cid))
        if not scored:
            return
        scored.sort(reverse=True)
        best_id = scored[0][1]
        self.chains[b.chain_idx].append(best_id)
        sel = self.selector
        if isinstance(sel, RPUCGSelector):
            parent_depth = max((sel.depth.get(p, 0) for p in b.parent_ids), default=0)
            sel.depth[best_id] = parent_depth + 1

    # ---- reporting -------------------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = "code") -> Ranking:
        scores = {}
        for ind in ctx.store.of_kind(kind or self.kind):
            s = score_of(ind, self.score_key)
            if s is not None:
                scores[ind.id] = s
        return Ranking.by_score(scores)

    # ---- persistence -----------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        st: Dict[str, Any] = {
            "chains": [list(c) for c in self.chains],
            "failures": [dict(f) for f in self.failures],
            "prompt_count": dict(self.prompt_count),
            "prompt_budget": dict(self.prompt_budget),
            "next_chain": self._next_chain,
            "selector": self.selector.name,
        }
        if isinstance(self.selector, PUCTSelector):
            st["visits"] = dict(self.selector.visits)
        if isinstance(self.selector, RPUCGSelector):
            st["depth"] = dict(self.selector.depth)
        return st

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.chains = [list(c) for c in state.get("chains", self.chains)]
        f = state.get("failures", [])
        if isinstance(f, dict):        # a checkpoint from before failures were per chain
            f = [dict(f) for _ in range(self.num_chains)]
        self.failures = [dict(x) for x in f] or [{} for _ in range(self.num_chains)]
        self.prompt_count = {int(k): int(v) for k, v in state.get("prompt_count", {}).items()}
        self.prompt_budget = {int(k): int(v) for k, v in state.get("prompt_budget", {}).items()}
        self._next_chain = state.get("next_chain", 0)
        if isinstance(self.selector, PUCTSelector):
            self.selector.visits = dict(state.get("visits", {}))
        if isinstance(self.selector, RPUCGSelector):
            self.selector.depth = dict(state.get("depth", {}))

    def reconcile(self, ctx: EvolveContext) -> None:
        """Drop chain entries whose individuals are not in the restored store, and forget batches
        that were in flight when the checkpoint was taken -- their children will never arrive."""
        self.chains = [[i for i in c if i in ctx.store] for c in self.chains]
        self.open.clear()

    # ---- the operator this algorithm is defined with ---------------------

    def default_variator(self, *, model: str = "high", timeout: float = 600,
                         target_file: Optional[str] = None, **kw):
        """A single completion producing k candidates -- no tools, no workspace, no self-scoring.

        This is half of what SimpleTES is. Swapping in an agent that can run the evaluator before
        it commits changes the algorithm, not just its speed, so the method names its own operator
        instead of accepting whatever the caller wired up.
        """
        from ..variators.completion import CompletionVariator

        return CompletionVariator(model=model, timeout=timeout, target_file=target_file,
                                  score_key=self.score_key)

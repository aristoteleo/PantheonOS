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

All three arithmetic selectors are ported: `balance` (a stratified draw), `puct` (value plus an
exploration bonus, per chain) and `rpucg` (percentile ranks over a gamma-decayed value propagated
through the whole DAG, with anti-inbreeding). They differ in nothing but how parents are chosen,
which is the axis upstream varies to get its family of algorithms.

Still not ported, deliberately: `reflection_mode` (an extra model call summarising each winner) and
the `llm_elite` / `llm_refine_*` selectors (a second LLM pass that re-ranks a shortlist). Both add
model calls to the SELECTION step, which is a different kind of algorithm from the three here, and
neither is needed to test the seam.
"""
from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..core.genome import CodeGenome
from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext
from ..variators.completion import CompletionVariator, EvolveBlock


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

    def observe(self, ctx: EvolveContext, score_key: str) -> None:
        """Called once per `ask`, before any picking. A policy that scores against the whole
        population rather than one chain computes that view here."""

    def on_commit(self, chain_idx: int, parent_ids: Sequence[str], best_score: float) -> None:
        """Called once a batch has resolved. `balance` keeps no state; the tree policies do."""

    def state_dict(self) -> Dict[str, Any]:
        return {}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        pass

    def pick(self, chain: List[Individual], n: int, rng: random.Random,
             key: str, chain_idx: int = 0) -> List[Individual]:
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
    """`Q(s) + c * scale * P(s) * sqrt(1+T) / (1+n(s))` -- policies/puct.py.

    Four things the first version of this port dropped, each of which changes what gets picked:

      - **scale**, the chain's score range `r_max - r_min`. Without it the exploration term is in
        raw units while Q is a score in [0, 1], so on a problem whose scores span 0.3 the bonus
        comes out several times the value it is meant to perturb and the selection degenerates into
        round-robin over visit counts. This is the one that matters most.
      - **P(s)**, a rank prior falling off linearly with rank, so the exploration budget is not
        handed to the worst node in the chain on the same terms as the best.
      - **Q(s) = max(own score, the best any child of it reached)**. A node whose children did well
        is worth returning to even if it scored poorly itself; that is the backpropagation, and
        without it Q is just the node's own score.
      - visits kept **per chain** and incremented **on commit**, for the parents actually used.
        Counting them at selection time marks a node explored before its batch has reported
        anything, and sharing one dict across chains lets one chain's history suppress another's.
    """

    name = "puct"

    def __init__(self, c: float = 1.0, **kw):
        super().__init__(**kw)
        self.c = c
        self.visits: Dict[int, Dict[str, int]] = {}
        self.max_child: Dict[int, Dict[str, float]] = {}
        self.expansions: Dict[int, int] = {}

    def terms(self, chain: List[Individual], key: str,
              chain_idx: int) -> List[tuple]:
        """`(Q, bonus)` per node, in the chain's own order -- which is best first.

        Split rather than summed because the split is the explanation: how much of a node's
        standing is what it scored, and how much is that nobody has tried it lately.
        """
        n = len(chain)
        if not n:
            return []
        hi = score_of(chain[0], key) or 0.0
        lo = score_of(chain[-1], key) or 0.0
        scale = max(hi - lo, 1e-6)
        rank_sum = n * (n + 1) / 2
        visits = self.visits.get(chain_idx, {})
        mc = self.max_child.get(chain_idx, {})
        t = self.expansions.get(chain_idx, 0)

        out = []
        for rank, node in enumerate(chain):
            own = score_of(node, key)
            q = max(own if own is not None else -math.inf, mc.get(node.id, -math.inf))
            if q == -math.inf:
                q = 0.0
            prior = (n - rank) / rank_sum if rank_sum else 1.0 / n
            bonus = self.c * scale * prior * math.sqrt(1 + t) / (1 + visits.get(node.id, 0))
            out.append((q, bonus))
        return out

    def pick(self, chain, n, rng, key, chain_idx: int = 0):
        if not chain:
            return []
        u = [q + b for q, b in self.terms(chain, key, chain_idx)]
        order = sorted(range(len(chain)), key=lambda i: u[i], reverse=True)[:n]
        return [chain[i] for i in order]

    def on_commit(self, chain_idx: int, parent_ids: Sequence[str], best_score: float) -> None:
        visits = self.visits.setdefault(chain_idx, {})
        mc = self.max_child.setdefault(chain_idx, {})
        for pid in parent_ids:
            mc[pid] = max(mc.get(pid, -math.inf), best_score)
            visits[pid] = visits.get(pid, 0) + 1
        self.expansions[chain_idx] = self.expansions.get(chain_idx, 0) + 1

    def state_dict(self) -> Dict[str, Any]:
        return {"visits": {str(k): dict(v) for k, v in self.visits.items()},
                "max_child": {str(k): dict(v) for k, v in self.max_child.items()},
                "expansions": {str(k): v for k, v in self.expansions.items()}}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.visits = {int(k): dict(v) for k, v in state.get("visits", {}).items()}
        self.max_child = {int(k): dict(v) for k, v in state.get("max_child", {}).items()}
        self.expansions = {int(k): int(v) for k, v in state.get("expansions", {}).items()}


def percentile_ranks(values: Dict[str, float]) -> Dict[str, float]:
    """Each entry's position in [0, 1) among all of them -- `bisect_left(sorted, v) / n`."""
    if not values:
        return {}
    ordered = sorted(values.values())
    n = len(ordered)
    return {k: bisect.bisect_left(ordered, v) / n for k, v in values.items()}


class RPUCGSelector(PUCTSelector):
    """`Q(s) + c * P(s) * sqrt(1+T) / (1+n(s))` on percentile ranks -- policies/rpucg.py.

    Three things separate it from PUCT, and none of them is a tweak:

      - **V, not the score.** `V(s) = max(raw(s), gamma * max over children of V(c))`, propagated
        bottom-up through the whole parent->child DAG. A node is worth what the best thing
        descended from it is worth, discounted once per generation of distance -- so a node three
        steps upstream of the run's best program still carries `gamma**3` of it. PUCT's
        `max_child_reward` only reaches one hop and never decays.
      - **Percentile ranks.** Q and P are both a node's rank among the whole population, in [0, 1).
        That is why there is no `scale` factor here: the units are already comparable, and the
        exploration term cannot be swamped by a problem whose scores happen to span 0.02.
      - **Anti-inbreeding.** Taking a node removes its parents and its children from the running,
        so one prompt is not built out of a single family line.

    P ranks the RAW score while Q ranks V, which is the point of having both: a node can be
    valuable because of what came after it (high Q) while itself being unremarkable (low P), and
    the exploration budget follows P.
    """

    name = "rpucg"

    def __init__(self, c: float = 1.0, gamma: float = 0.8, **kw):
        super().__init__(c, **kw)
        self.gamma = gamma
        self._q: Dict[str, float] = {}
        self._p: Dict[str, float] = {}
        self._kin: Dict[str, set] = {}

    def observe(self, ctx: EvolveContext, score_key: str) -> None:
        """Recompute the population-wide view. Called once per `ask`.

        It has to be the whole store and not the chain: V propagates along real descent edges,
        which cross chains, and a percentile rank over five nodes is not a percentile rank.
        """
        pop = list(ctx.store)
        children: Dict[str, set] = {}
        for ind in pop:
            for pid in ind.parent_ids:
                children.setdefault(pid, set()).add(ind.id)

        raw: Dict[str, float] = {}
        for ind in pop:
            s = score_of(ind, score_key)
            raw[ind.id] = -math.inf if s is None else s

        # Children before parents, so a parent's V is computed from finished values.
        v: Dict[str, float] = {}
        for ind in sorted(pop, key=lambda i: i.order, reverse=True):
            kids = children.get(ind.id)
            best = max((v.get(k, -math.inf) for k in kids), default=-math.inf) if kids else -math.inf
            v[ind.id] = max(raw[ind.id], self.gamma * best)

        self._q = percentile_ranks(v)
        self._p = percentile_ranks(raw)
        self._kin = {ind.id: set(ind.parent_ids) | children.get(ind.id, set()) for ind in pop}

    def terms(self, chain, key, chain_idx):
        visits = self.visits.get(chain_idx, {})
        t = self.expansions.get(chain_idx, 0)
        out = []
        for node in chain:
            q = self._q.get(node.id, 0.0)
            p = self._p.get(node.id, 0.0)
            out.append((q, self.c * p * math.sqrt(1 + t) / (1 + visits.get(node.id, 0))))
        return out

    def pick(self, chain, n, rng, key, chain_idx: int = 0):
        if not chain:
            return []
        u = [q + b for q, b in self.terms(chain, key, chain_idx)]
        picked: List[Individual] = []
        blocked: set = set()
        for i in sorted(range(len(chain)), key=lambda i: u[i], reverse=True):
            nd = chain[i]
            if nd.id in blocked:
                continue
            picked.append(nd)
            if len(picked) >= n:
                break
            blocked.add(nd.id)
            blocked |= self._kin.get(nd.id, set())
        return picked

    def on_commit(self, chain_idx: int, parent_ids: Sequence[str], best_score: float) -> None:
        # No `max_child_reward`: the V propagation in `observe` is this policy's backpropagation,
        # and it reads the store directly. Only the visit counts move here.
        visits = self.visits.setdefault(chain_idx, {})
        for pid in parent_ids:
            visits[pid] = visits.get(pid, 0) + 1
        self.expansions[chain_idx] = self.expansions.get(chain_idx, 0) + 1

    def state_dict(self) -> Dict[str, Any]:
        # `_q`, `_p` and `_kin` are derived from the store on every `ask`, so they are not state.
        return {**super().state_dict(), "gamma": self.gamma}

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        super().load_state_dict(state)
        self.gamma = float(state.get("gamma", self.gamma))


SELECTORS = {s.name: s for s in (Selector, PUCTSelector, RPUCGSelector)}


@dataclass
class _Batch:
    """One prompt's worth of candidates, open until all k have resolved."""

    chain_idx: int
    parent_ids: List[str]
    k: int
    done: int = 0
    children: List[str] = field(default_factory=list)



class UpstreamCompletionVariator(CompletionVariator):
    """SimpleTES's operator, words and all: the authors' generation query reproduced
    (their `GENERATION_PROMPT_TEMPLATE`, commit a19a54b1). No system message on the wire,
    `Task:` header, a language-named reply rule, tagged fences, inspirations as FULL
    programs with their complete metric dicts sorted by score, their section headers and
    their four-bullet strategy. The base class contributes only machinery -- the call, the
    n-shortfall fallback, EVOLVE-BLOCK splitting/merging.

    Not reproduced: upstream's per-node reflection paragraphs -- an engine feature (one
    extra LLM call per evaluated node), not prompt text. Marker-less seeds fall back to the
    base whole-file prompt, which is upstream's behaviour for unmarked programs too.
    """

    SYSTEM = ""

    def _inspiration(self, index: int, ind, code: str, tag: str) -> str:
        """One inspiration, upstream's `INSPIRATION_TEMPLATE`: full metrics, full code."""
        m = ind.metrics() or {}
        lines = []
        for k, v in m.items():
            if k == "error":
                lines.append(f"  {k}: {str(v)[:240]}")
            elif isinstance(v, float):
                lines.append(f"  {k}: {v:.6f}")
            else:
                lines.append(f"  {k}: {v}")
        return (f"\n--- Inspiration {index} ---\n"
                f"Score: {m.get(self.score_key)}\n"
                f"Metrics:\n" + "\n".join(lines) +
                f"\nCode:\n```{tag}\n{code}\n```\n")

    def build_block_prompt(self, ctx: EvolveContext, item: Create, path: str,
                                    eb: "EvolveBlock") -> str:
        """The authors' `GENERATION_PROMPT_TEMPLATE`, reproduced: same headers, same rule list
        (language-named), same inspiration blocks -- each parent as its FULL program with its
        complete metric dict, sorted by score -- same failure-pattern section and the same
        four-bullet strategy. No chain-history digest: upstream carries history through the
        inspirations, so adding ours would be a departure, not a translation."""
        c = item.context
        lang_name, tag = self._lang(path)

        def _sc(ind):
            v = ind.metrics().get(self.score_key)
            return v if v is not None else float("-inf")

        insp = sorted(c.parents, key=_sc, reverse=True)
        chunks = []
        for i, pr in enumerate(insp, 1):
            src = pr.genome.files.get(path) if isinstance(pr.genome, CodeGenome) else None
            chunks.append(self._inspiration(i, pr, src or pr.genome.render(), tag))
        failure_text = ""
        if c.failures:
            worst = sorted(c.failures.items(), key=lambda kv: -kv[1])[:5]
            failure_text = ("\n[FAILURE PATTERNS] (common errors to avoid)\n" +
                            "\n".join(f"- {k} (x{int(v)})" for k, v in worst) + "\n")
        block_word = f"{lang_name} code block" if lang_name else "code block"
        return (
            f"Task: {c.instruction or ctx.objective}\n\n"
            "Generation instruction (must follow exactly):\n"
            f"1) Only the code between `{eb.start_line}` and `{eb.end_line}` is extracted.\n"
            "2) The final program is reconstructed as EXACT_PREFIX + evolved_block + "
            "EXACT_SUFFIX.\n"
            "3) Keep marker lines exactly as written.\n"
            f"4) Return one {block_word} that includes both EVOLVE-BLOCK markers.\n\n"
            f"EXACT_PREFIX (kept unchanged):\n```{tag}\n{eb.prefix.rstrip(chr(10))}\n```\n\n"
            f"EXACT_SUFFIX (kept unchanged):\n```{tag}\n{eb.suffix.rstrip(chr(10))}\n```\n\n"
            "=== REFERENCE SOLUTIONS ===\n\n"
            f"[SAMPLED INSPIRATIONS] ({len(insp)} solutions sampled for detailed reference)\n"
            "Learn from these specific implementations - study their patterns and techniques.\n"
            + "".join(chunks) + failure_text +
            "\n=== GENERATION STRATEGY ===\n"
            "- Prioritize NOVEL approaches not yet seen in the elite pool\n"
            "- Only refine existing approaches if you identify clear improvement potential\n"
            "- Combine insights from multiple solutions when beneficial\n"
            "- Avoid the listed failure patterns\n\n"
            "Generate an improved solution with higher score:\n")


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
        self.selector.observe(ctx, self.score_key)
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
                                        self.rng, self.score_key, c)
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
        best_score, best_id = scored[0]
        self.chains[b.chain_idx].append(best_id)
        # Upstream updates the tree statistics HERE, not at selection: a parent is "visited" once
        # its batch has come back, and the batch's best score backpropagates to every parent it
        # was built from.
        self.selector.on_commit(b.chain_idx, b.parent_ids, best_score)

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
            # The selector owns its own state now -- it has three fields under puct and the
            # method has no business knowing their names.
            "selector_state": self.selector.state_dict(),
        }
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
        self.selector.load_state_dict(state.get("selector_state", {}))

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
        # max_tokens matches upstream's default (EngineConfig.max_tokens = 32768). Left unset,
        # the gateway's own output cap decides whether a 43KB program can even be emitted whole,
        # and that decision then wears the algorithm's name.
        return UpstreamCompletionVariator(model=model, timeout=timeout,
                                          target_file=target_file,
                                          score_key=self.score_key, max_tokens=32768,
                                          reasoning_max_tokens=kw.get(
                                              "reasoning_max_tokens"))

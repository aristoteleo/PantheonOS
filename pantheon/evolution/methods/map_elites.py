"""The algorithm Pantheon-Evolve runs today, written against the method interface.

This is a port, not a redesign: MAP-Elites over islands with periodic migration, exactly as
`EvolutionDatabase` implements it. It exists so the existing algorithm survives the move -- the
21 tests in `test_evolution_search_semantics.py` describe that behaviour and this has to match it.

One thing does change, deliberately. Fitness is computed here rather than on the individual.
`Program.fitness_score()` folds raw metrics into a scalar using weights the evaluator supplied,
which works only while fitness is a fixed property of a thing; it is not, and the same file's
tests already pin that it moves as the observed metric ranges widen. Ranking is a property of the
search, so it lives with the search, and Pareto or adversarial methods are then free to rank
differently without fighting the individual.

The bins are recomputed from scratch whenever the observed feature ranges widen, matching the
current dynamic-range behaviour. That is O(n) on a widening and is why the original caches them.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.individual import Individual, Ranking
from ..core.method import BaseMethod, EvolveContext
from ..core.work import Create, Failure, Measurement, PromptContext
from ..utils.metrics import compute_features


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


class MapElitesIslands(BaseMethod):
    """Quality-diversity over a feature grid, replicated across islands."""

    name = "map_elites_islands"

    def __init__(
        self,
        num_islands: int = 3,
        feature_dimensions: Optional[List[str]] = None,
        feature_bins: int = 10,
        feature_range_padding: float = 0.1,
        exploration_ratio: float = 0.2,
        archive_ratio: float = 0.25,
        num_inspirations: int = 2,
        num_top_programs: int = 3,
        migration_interval: int = 20,
        migration_rate: float = 0.1,
        function_weight: float = 0.8,
        llm_weight: float = 0.2,
        kind: str = "code",
        seed: int = 0,
    ):
        self.num_islands = num_islands
        self.feature_dimensions = list(feature_dimensions or ["complexity", "diversity"])
        self.feature_bins = feature_bins
        self.padding = feature_range_padding
        self.exploration_ratio = exploration_ratio
        self.archive_ratio = archive_ratio
        self.num_inspirations = num_inspirations
        self.num_top_programs = num_top_programs
        self.migration_interval = migration_interval
        self.migration_rate = migration_rate
        self.function_weight = function_weight
        self.llm_weight = llm_weight
        self.kind = kind
        self.rng = random.Random(seed)

        self.islands: List[set] = [set() for _ in range(num_islands)]
        self.island_of: Dict[str, int] = {}
        self.coords: Dict[str, Dict[str, float]] = {}
        self.metric_ranges: Dict[str, Tuple[float, float]] = {}
        self.feature_ranges: Dict[str, Tuple[float, float]] = {}
        self.elites: Dict[Tuple[int, Tuple[int, ...]], str] = {}
        self.best_id: Optional[str] = None
        self.admitted = 0
        self.admitted_ids: set = set()
        """Which individuals took a bin slot. Kept because "was this child accepted" is a fact
        about the search that reporting needs and cannot recover afterwards -- the grid only
        remembers who holds a bin NOW, not who ever did."""
        self.steps = 0

    # ---- fitness ---------------------------------------------------------

    def function_score(self, metrics: Dict[str, Any]) -> float:
        """Weighted mean of the evaluator's metrics, each normalised to its observed range.

        `fitness_weights` comes from the evaluator, not from config: which metrics matter, and how
        much, is a property of the problem. Without it there is nothing to optimise and the score
        is 0 -- the same convention the current code uses to mark a failed evaluation.
        """
        weights = metrics.get("fitness_weights")
        if not isinstance(weights, dict) or not weights:
            return 0.0
        total = num = 0.0
        for name, w in weights.items():
            v = metrics.get(name)
            if not isinstance(v, (int, float)):
                continue
            lo, hi = self.metric_ranges.get(name, (v, v))
            norm = 0.5 if hi - lo < 1e-12 else (float(v) - lo) / (hi - lo)
            num += _clamp01(norm) * float(w)
            total += float(w)
        return num / total if total > 1e-12 else 0.0

    def fitness(self, ind: Optional[Individual]) -> float:
        if ind is None:
            return 0.0
        metrics = ind.metrics()
        if not metrics:
            return 0.0
        fs = self.function_score(metrics)
        llm = metrics.get("llm_score", 0.5)
        tw = self.function_weight + self.llm_weight
        if tw < 1e-8:
            return fs
        return (fs * self.function_weight + float(llm) * self.llm_weight) / tw

    # ---- grid ------------------------------------------------------------

    def _features(self, ind: Individual) -> Dict[str, float]:
        """Feature coordinates: metric values when the evaluator reports them, code features
        otherwise. Matches `Program.feature_coordinates`."""
        out: Dict[str, float] = {}
        metrics = ind.metrics()
        from_code = []
        for dim in self.feature_dimensions:
            if dim in metrics and isinstance(metrics[dim], (int, float)):
                out[dim] = _clamp01(metrics[dim])
            else:
                from_code.append(dim)
        if from_code:
            text = ind.genome.render()
            try:
                out.update(compute_features(text, from_code, []))
            except Exception:
                out.update({d: 0.5 for d in from_code})
        return out

    def _widen(self, ranges: Dict[str, Tuple[float, float]], key: str, v: float) -> bool:
        lo, hi = ranges.get(key, (v, v))
        new = (min(lo, v), max(hi, v))
        if new != (lo, hi) or key not in ranges:
            ranges[key] = new
            return True
        return False

    def _bin(self, coords: Dict[str, float]) -> Tuple[int, ...]:
        out = []
        for dim in self.feature_dimensions:
            v = coords.get(dim, 0.5)
            lo, hi = self.feature_ranges.get(dim, (0.0, 1.0))
            span = hi - lo
            pad = span * self.padding
            lo, hi = lo - pad, hi + pad
            frac = 0.5 if hi - lo < 1e-12 else (v - lo) / (hi - lo)
            out.append(max(0, min(self.feature_bins - 1, int(frac * self.feature_bins))))
        return tuple(out)

    def _rebuild_elites(self, ctx: EvolveContext) -> None:
        """Recompute every bin from scratch. Needed when a range widens, because the bin a
        program falls into is a function of the range, so old assignments go stale."""
        self.elites.clear()
        for ind_id, coords in self.coords.items():
            isl = self.island_of.get(ind_id)
            if isl is None:
                continue
            key = (isl, self._bin(coords))
            cur = self.elites.get(key)
            if cur is None or self.fitness(ctx.store.get(ind_id)) > self.fitness(ctx.store.get(cur)):
                self.elites[key] = ind_id

    # ---- lifecycle -------------------------------------------------------

    async def start(self, ctx: EvolveContext, seeds: Sequence[Individual]) -> None:
        for s in seeds:
            self._place(ctx, s, island=0)

    def _place(self, ctx: EvolveContext, ind: Individual, island: Optional[int] = None) -> bool:
        """Record an individual on the grid, and report whether it is elite in its bin."""
        isl = island if island is not None else self.rng.randrange(self.num_islands)
        self.islands[isl].add(ind.id)
        self.island_of[ind.id] = isl

        widened = False
        for k, v in ind.metrics().items():
            if isinstance(v, (int, float)) and k != "fitness_weights":
                widened |= self._widen(self.metric_ranges, k, float(v))
        coords = self._features(ind)
        self.coords[ind.id] = coords
        for dim, v in coords.items():
            widened |= self._widen(self.feature_ranges, dim, v)

        if widened:
            self._rebuild_elites(ctx)

        key = (isl, self._bin(coords))
        holder = self.elites.get(key)
        mine = self.fitness(ind)
        if holder is None or holder == ind.id or mine > self.fitness(ctx.store.get(holder)):
            self.elites[key] = ind.id
            admitted = True
        else:
            admitted = False

        if self.best_id is None or mine > self.fitness(ctx.store.get(self.best_id)):
            self.best_id = ind.id
        if admitted:
            self.admitted += 1
            self.admitted_ids.add(ind.id)
        return admitted

    # ---- selection -------------------------------------------------------

    def _sample_parent(self, ctx: EvolveContext) -> Optional[Individual]:
        """Explore uniformly with probability `exploration_ratio`, otherwise take a bin elite.

        Sampling elites rather than the raw population is what keeps the grid's diversity in play:
        every filled bin is equally likely regardless of how many programs landed in it.
        """
        pool = [i for i in self.island_of if ctx.store.get(i) is not None]
        if not pool:
            return None
        if self.rng.random() < self.exploration_ratio or not self.elites:
            return ctx.store.get(self.rng.choice(pool))
        return ctx.store.get(self.rng.choice(list(self.elites.values())))

    def _inspirations(self, ctx: EvolveContext, parent: Individual, n: int) -> List[Individual]:
        """Top performers plus a random elite, never the parent itself."""
        if n <= 0:
            return []
        ranked = sorted(
            (i for i in (ctx.store.get(x) for x in self.island_of) if i and i.id != parent.id),
            key=self.fitness, reverse=True,
        )
        top = ranked[: self.num_top_programs]
        rest = [i for i in ranked[self.num_top_programs:]]
        self.rng.shuffle(rest)
        return (top + rest)[:n]

    async def ask(self, ctx: EvolveContext, n: int) -> List[Create]:
        items: List[Create] = []
        for _ in range(max(0, n)):
            parent = self._sample_parent(ctx)
            if parent is None:
                break
            insp = self._inspirations(ctx, parent, self.num_inspirations)
            items.append(Create(
                kind=self.kind,
                parent_ids=[parent.id],
                k=1,
                context=PromptContext(
                    instruction=ctx.objective,
                    parents=[parent],
                    inspirations=insp,
                    history=self._history(ctx, parent),
                ),
                meta={"island": self.island_of.get(parent.id, 0)},
            ))
        return items

    def _history(self, ctx: EvolveContext, parent: Individual) -> str:
        lines = []
        for a in ctx.store.ancestors(parent.id)[:4]:
            lines.append(f"- {a.id}: fitness={self.fitness(a):.4f} {a.meta.get('summary', '')}")
        return "\n".join(lines)

    # ---- feedback --------------------------------------------------------

    async def on_measured(self, ctx: EvolveContext, ind: Individual, m: Measurement) -> None:
        island = None
        parent = ctx.store.get(ind.parent_id) if ind.parent_id else None
        if parent is not None:
            island = self.island_of.get(parent.id)          # children stay on the parent's island
        self._place(ctx, ind, island=island)
        self.steps += 1
        if self.migration_interval and self.steps % self.migration_interval == 0:
            self._migrate()

    async def on_failed(self, ctx: EvolveContext, f: Failure) -> None:
        self.steps += 1

    def _migrate(self) -> None:
        """Move a fraction of each island's population to its neighbour, as the current code does
        on a fixed interval. Without it the islands never exchange anything and the model is just
        N independent runs."""
        if self.num_islands < 2:
            return
        # snapshot first: moving into island i+1 while still iterating would hand the same
        # individuals to the next island's turn, which sends them straight back
        before = [sorted(pop) for pop in self.islands]
        for i, pop in enumerate(before):
            if not pop:
                continue
            k = max(1, int(len(pop) * self.migration_rate))
            movers = self.rng.sample(pop, min(k, len(pop)))
            target = (i + 1) % self.num_islands
            for mid in movers:
                self.islands[i].discard(mid)
                self.islands[target].add(mid)
                self.island_of[mid] = target

    # ---- reporting -------------------------------------------------------

    def rank(self, ctx: EvolveContext, kind: str = "code") -> Ranking:
        scores = {
            i.id: self.fitness(i)
            for i in ctx.store.of_kind(kind or self.kind)
            if i.is_measured()
        }
        return Ranking.by_score(scores)

    def coverage(self) -> float:
        """Filled fraction of the grid -- the quality-diversity number worth reporting."""
        total = self.num_islands * (self.feature_bins ** len(self.feature_dimensions))
        return len(self.elites) / total if total else 0.0

    # ---- persistence -----------------------------------------------------

    def state_dict(self) -> Dict[str, Any]:
        return {
            "islands": [sorted(s) for s in self.islands],
            "island_of": dict(self.island_of),
            "coords": {k: dict(v) for k, v in self.coords.items()},
            "metric_ranges": {k: list(v) for k, v in self.metric_ranges.items()},
            "feature_ranges": {k: list(v) for k, v in self.feature_ranges.items()},
            "elites": {f"{i}|{','.join(map(str, b))}": v for (i, b), v in self.elites.items()},
            "best_id": self.best_id,
            "steps": self.steps,
            "admitted": self.admitted,
            "admitted_ids": sorted(self.admitted_ids),
        }

    def load_state_dict(self, state: Dict[str, Any]) -> None:
        self.islands = [set(s) for s in state.get("islands", [])] or self.islands
        self.island_of = dict(state.get("island_of", {}))
        self.coords = {k: dict(v) for k, v in state.get("coords", {}).items()}
        self.metric_ranges = {k: tuple(v) for k, v in state.get("metric_ranges", {}).items()}
        self.feature_ranges = {k: tuple(v) for k, v in state.get("feature_ranges", {}).items()}
        self.elites = {}
        for key, v in state.get("elites", {}).items():
            isl, bins = key.split("|", 1)
            self.elites[(int(isl), tuple(int(x) for x in bins.split(",") if x != ""))] = v
        self.best_id = state.get("best_id")
        self.steps = state.get("steps", 0)
        self.admitted = state.get("admitted", 0)
        self.admitted_ids = set(state.get("admitted_ids", []))

    # ---- the operator this algorithm is defined with ---------------------

    def default_variator(self, *, evaluator=None, model: str = "high", sandbox: bool = False,
                         **kw):
        """A coding agent that edits a workspace and verifies its edit before submitting.

        This is what Pantheon-Evolve has always meant by a mutation, and the reported results were
        produced with it, so it is the operator this method is defined with. `sandbox=True` runs
        the same agent off-host, which changes where code executes but not what the search does.
        """
        if sandbox:
            from ..variators.sandbox import SandboxVariator

            return SandboxVariator(evaluator_code=kw.get("evaluator_code", ""), model=model,
                                   timeout=int(kw.get("timeout", 1800)))
        from ..variators.agent import AgentVariator, agent_kwargs

        # Forwarded from the operator's own signature, not from a list kept here. Two knobs have
        # already been lost to that list going stale.
        return AgentVariator(evaluator=evaluator,
                             **{"model": model, "timeout": 1800, **agent_kwargs(kw)})

    def reconcile(self, ctx: EvolveContext) -> None:
        """Drop grid entries for individuals the restored store does not have, then rebuild the
        bins -- they are derived from ranges that may have been restored alongside a partial set."""
        self.island_of = {k: v for k, v in self.island_of.items() if k in ctx.store}
        self.coords = {k: v for k, v in self.coords.items() if k in ctx.store}
        self.islands = [{i for i in s if i in ctx.store} for s in self.islands]
        self._rebuild_elites(ctx)
        if self.best_id not in ctx.store:
            live = [i for i in self.island_of]
            self.best_id = max(live, key=lambda i: self.fitness(ctx.store.get(i)), default=None)

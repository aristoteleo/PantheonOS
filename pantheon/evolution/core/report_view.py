"""Presenting a new-format run in the shape the visualiser reads.

The visualiser is 4,300 lines of HTML generation that asks a database for `programs`,
`metric_ranges`, `best_program_id`, an archive and a `compute_function_score`. None of that is
wrong -- it is just written against the object the loop used to own. Rewriting it to consume
`Store` and `EvolveMethod` would be a large edit to working code for no gain in what the report
shows.

So this adapts instead: it reads a checkpoint written by `core.persistence` and exposes the same
handful of attributes. Two consequences worth naming:

  - fitness now belongs to the method, so `compute_function_score` here delegates to the method
    rather than recomputing a formula that would drift
  - a method with no grid (SimpleTES has chains, not bins) simply reports an empty archive, which
    is honest -- the report shows no coverage because there is none, rather than inventing one
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import EvolutionConfig
from ..program import CodebaseSnapshot, Program
from .genome import CodeGenome
from .individual import Individual, Store
from .persistence import load_run


def individual_to_program(ind: Individual, *, fitness: float = 0.0,
                          island: int = 0) -> Program:
    """An `Individual` in `Program` clothing, for code that predates the generalisation."""
    genome = ind.genome
    snapshot = (genome.to_snapshot() if isinstance(genome, CodeGenome)
                else CodebaseSnapshot.from_single_file("main.py", genome.render()))
    p = Program(
        id=ind.id,
        snapshot=snapshot,
        parent_id=ind.parent_id,
        generation=ind.generation,
        metrics=dict(ind.metrics()),
        mutation_summary=ind.meta.get("summary", ""),
    )
    p.order = ind.order
    p.island_id = island
    p.mutation_category = ind.meta.get("mutation_category", "")
    m = ind.measured()
    if m is not None:
        p.artifacts = dict(m.artifacts)
    return p


class RunView:
    """Duck-types the parts of `EvolutionDatabase` the visualiser touches."""

    def __init__(self, store: Store, method: Any, config: Optional[EvolutionConfig] = None,
                 objective: str = ""):
        self.store = store
        self.method = method
        self.objective = objective
        self.config = config or EvolutionConfig(
            feature_dimensions=list(getattr(method, "feature_dimensions", []) or
                                    ["complexity", "diversity"]),
            num_islands=getattr(method, "num_islands", 1),
            feature_bins=getattr(method, "feature_bins", 10),
            function_weight=getattr(method, "function_weight", 1.0),
            llm_weight=getattr(method, "llm_weight", 0.0),
        )
        island_of = getattr(method, "island_of", {})
        fitness = getattr(method, "fitness", lambda i: 0.0)
        self.programs: Dict[str, Program] = {
            ind.id: individual_to_program(ind, fitness=fitness(ind),
                                          island=island_of.get(ind.id, 0))
            for ind in sorted(store, key=lambda i: i.order)
        }
        self.metric_ranges: Dict[str, Tuple[float, float]] = dict(
            getattr(method, "metric_ranges", {}) or {})
        self.best_program_id: Optional[str] = getattr(method, "best_id", None)
        self.archive: List[str] = sorted(set(getattr(method, "elites", {}).values()))
        self.total_added = len(self.programs)
        self.total_improved = len(getattr(method, "admitted_ids", ()) or ())

    def compute_function_score(self, metrics: Dict[str, Any], *args, **kwargs) -> float:
        """Delegate to the method: it owns the definition, and duplicating it here would drift."""
        fn = getattr(self.method, "function_score", None)
        return float(fn(metrics)) if fn else 0.0

    def get_best_program(self) -> Optional[Program]:
        return self.programs.get(self.best_program_id or "")

    def iter_filled_bins(self, island_id: int):
        """`(bin coordinates, occupant id)` for one island -- what the grid heat-map draws.

        A method that keeps no grid yields nothing, and the report shows an empty map. That is the
        truthful rendering: SimpleTES has chains, not bins, and drawing a grid for it would be
        inventing a structure the search does not have.
        """
        for (isl, coords), prog_id in getattr(self.method, "elites", {}).items():
            if isl == island_id:
                yield coords, prog_id

    def get_feature_range(self, dim: str) -> Tuple[float, float]:
        return tuple(getattr(self.method, "feature_ranges", {}).get(dim, (0.0, 1.0)))

    def get_effective_feature_ranges(self) -> Dict[str, Tuple[float, float]]:
        return {d: self.get_feature_range(d) for d in self.config.feature_dimensions}

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "total_programs": len(self.programs),
            "total_added": self.total_added,
            "total_improved": self.total_improved,
            "islands": getattr(self.method, "num_islands", 1),
            "filled_bins": len(getattr(self.method, "elites", {})),
        }


def is_new_format(path: str) -> bool:
    return (Path(path) / "store.json").exists()


def load_view(path: str, method: Optional[Any] = None) -> RunView:
    """Load a `core.persistence` checkpoint into a view the visualiser can read.

    The method is rebuilt when not supplied. It has to be *some* method for fitness and the grid
    to mean anything, and MAP-Elites is the only one whose state the report was designed around --
    for another method the archive comes back empty rather than wrong.
    """
    store, state, meta = load_run(path)
    if method is None:
        from ..methods.agent_map_elites import AgentMapElites

        method = AgentMapElites()
        try:
            method.load_state_dict(state)
        except Exception:  # a state from a different method simply does not apply
            pass
    return RunView(store, method, objective=meta.get("objective", ""))

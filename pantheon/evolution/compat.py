"""`EvolutionTeam`, reduced to a shim over the new loop.

The class stays only as an import path. Every caller in the repo builds it the same way --
`EvolutionTeam(config=cfg)` then `await team.evolve(initial_code, evaluator_code, objective)` --
so that shape is preserved exactly while the 1899 lines behind it become a method, a variator and
a 224-line function.

What the config flags now select:

    single_agent_mutation / sandbox_mutation  ->  which Variator
    num_islands, feature_*, exploration_ratio ->  NicheMenu' parameters
    max_iterations, num_workers               ->  the loop's budget and concurrency

That mapping is the whole argument for the refactor: what used to be three `if` branches spread
across `_run_iteration`, `_worker` and `evolve` is now a choice of object made once, here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pantheon.utils.log import logger

from .config import EvolutionConfig
from .core.genome import CodeGenome
from .core.individual import Individual
from .core.loop import evolve as _evolve
from .core.method import Budget
from .methods.niche_menu import NicheMenu
from .program import CodebaseSnapshot, Program
from .result import EvolutionResult, IterationResult
from .variators.adapters import CodeEvaluator
from .variators.agent import AgentVariator
from .variators.sandbox import SandboxVariator


def method_from_config(config: EvolutionConfig) -> NicheMenu:
    return NicheMenu(
        num_islands=config.num_islands,
        feature_dimensions=list(config.feature_dimensions or []) or None,
        feature_bins=config.feature_bins,
        feature_range_padding=config.feature_range_padding,
        exploration_ratio=config.exploration_ratio,
        archive_ratio=config.archive_ratio,
        num_inspirations=config.num_inspirations,
        num_top_programs=config.num_top_programs,
        migration_interval=config.migration_interval,
        migration_rate=config.migration_rate,
        function_weight=config.function_weight,
        llm_weight=config.llm_weight,
    )


def variator_from_config(config: EvolutionConfig, evaluator: Any,
                         evaluator_code: str = "") -> Any:
    """`sandbox_mutation` picks the operator that runs the mutation off this machine."""
    if getattr(config, "sandbox_mutation", False):
        return SandboxVariator(
            evaluator_code=evaluator_code,
            model=config.mutator_model,
            system_prompt=getattr(config, "mutation_system_prompt", None),
            image_ref=getattr(config, "sandbox_image", "nanguage/pantheon-agents:latest"),
            timeout=int(config.mutation_timeout),
            send_inspirations=getattr(config, "sandbox_inspirations", False),
        )
    return AgentVariator(
        evaluator=evaluator,
        model=config.mutator_model,
        system_prompt=getattr(config, "mutation_system_prompt", None),
        workspace_root=config.workspace_path,
        max_evaluations=getattr(config, "max_evaluations_per_mutation", None),
        max_tool_calls=getattr(config, "max_tool_calls_per_mutation", None),
        max_turns=getattr(config, "max_mutation_turns", None),
        timeout=config.mutation_timeout,
        web_search=getattr(config, "mutation_web_search", False),
        warm_start_file=getattr(config, "warm_start_file", None),
    )


def _as_program(ind: Individual, method: NicheMenu) -> Program:
    """Present an `Individual` in the shape callers still expect."""
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
    p.island_id = method.island_of.get(ind.id, 0)
    return p


class EvolutionTeam:
    """Kept for its constructor signature. The behaviour lives in `core.loop.evolve`."""

    def __init__(
        self,
        mutator: Optional[Any] = None,
        evaluator: Optional[Any] = None,
        analyzer: Optional[Any] = None,
        critic: Optional[Any] = None,
        database: Optional[Any] = None,
        config: Optional[EvolutionConfig] = None,
    ):
        self.config = config or EvolutionConfig()
        if self.config.log_level:
            from pantheon.utils.log import set_level

            set_level(self.config.log_level)
        for name, given in (("mutator", mutator), ("analyzer", analyzer), ("critic", critic),
                            ("database", database)):
            if given is not None:
                logger.warning(f"EvolutionTeam({name}=...) is ignored by the new loop")
        self._evaluator = evaluator
        self.method: Optional[NicheMenu] = None
        self.objective = ""
        self.evaluator_code = ""

    async def evolve(
        self,
        initial_code: Union[str, CodebaseSnapshot],
        evaluator_code: str,
        objective: str,
        max_iterations: Optional[int] = None,
        initial_path: Optional[str] = None,
        resume_from: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        **kwargs,
    ) -> EvolutionResult:
        cfg = self.config
        self.objective, self.evaluator_code = objective, evaluator_code
        # `db_path` was where the old loop checkpointed; keep that meaning so callers that already
        # pass it keep getting checkpoints, and `resume_from` reads one back
        checkpoint_path = resume_from or cfg.db_path

        if isinstance(initial_code, CodebaseSnapshot):
            seed = CodeGenome.from_snapshot(initial_code)
        elif initial_path:
            seed = CodeGenome.from_snapshot(CodebaseSnapshot.from_directory(initial_path))
        else:
            seed = CodeGenome(files={"main.py": initial_code})

        evaluator = self._evaluator or CodeEvaluator(
            evaluator_code=evaluator_code,
            timeout=cfg.evaluation_timeout,
            workspace_path=cfg.workspace_path,
            llm_weight=cfg.llm_weight,
            max_parallel=cfg.max_parallel_evaluations,
        )
        self.method = method_from_config(cfg)
        variator = variator_from_config(cfg, evaluator, evaluator_code)

        result = EvolutionResult(config_used=cfg.to_dict())
        n, best_seen = 0, 0.0

        def on_event(kind: str, data: Dict[str, Any]) -> None:
            """Feed the old `progress_callback(iteration, best_score)` contract.

            The raw metric is used rather than the method's normalised fitness: fitness moves as
            observed ranges widen, so a progress line computed from it would appear to go
            backwards. Callers reading this want a monotone "best so far".
            """
            nonlocal n, best_seen
            if kind != "measured":
                return
            n += 1
            v = data.get("metrics", {}).get("combined_score")
            if isinstance(v, (int, float)):
                best_seen = max(best_seen, float(v))
            progress_callback(n, best_seen)

        res = await _evolve(
            method=self.method,
            variator=variator,
            evaluators={"code": evaluator},
            seeds=[seed],
            objective=objective,
            budget=Budget(max_items=max_iterations or cfg.max_iterations),
            concurrency=max(1, cfg.num_workers),
            on_event=on_event if progress_callback else None,
            checkpoint_path=checkpoint_path,
            checkpoint_every=max(1, cfg.checkpoint_interval),
            resume=bool(resume_from),
        )

        # Rebuild the old per-iteration record from what the run actually measured. The numbers
        # all exist -- the variator stamps its wall time and cost onto the child, the measurement
        # carries evaluation time, and the method remembers which children took a bin -- they just
        # have to be collected, because `EvolutionResult` predates all three.
        ranking = res.ranking
        children = [i for i in sorted(res.store, key=lambda x: x.order) if i.parent_ids]
        for ind in sorted(res.store, key=lambda i: i.order):
            s = ranking.scores.get(ind.id, 0.0)
            result.score_history.append(s)
            result.best_score_history.append(max(result.best_score_history + [s]))
        best = res.best
        if best is not None:
            result.best_program = _as_program(best, self.method)

        admitted_ids = getattr(self.method, "admitted_ids", set())
        for i, ind in enumerate(children):
            m = ind.measured()
            parent_score = ranking.scores.get(ind.parent_id, 0.0)
            child_score = ranking.scores.get(ind.id, 0.0)
            result.iteration_results.append(IterationResult(
                iteration=i,
                parent_id=ind.parent_id or "",
                child_id=ind.id,
                parent_score=parent_score,
                child_score=child_score,
                improvement=child_score - parent_score,
                accepted=ind.id in admitted_ids,
                mutation_time=float(ind.meta.get("mutation_seconds", 0.0)),
                evaluation_time=float(m.duration if m else 0.0),
                total_time=float(ind.meta.get("mutation_seconds", 0.0)) + float(m.duration if m else 0.0),
                llm_cost=float(ind.meta.get("cost", 0.0)),
            ))
        result.total_iterations = len(result.iteration_results)
        result.total_duration = res.seconds
        result.finalize()      # derives successful_iterations, improvements and total_cost
        self._last_run = res
        return result

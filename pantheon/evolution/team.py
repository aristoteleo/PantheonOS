"""Backwards-compatible import surface for what used to live here.

`EvolutionTeam` was 1899 lines that held four unrelated things at once: the agents that write
mutations, the problem definition, the search state, and the loop. Eight of its attributes were
scratch state for a single mutation, stored on an object every worker shared -- so with
`num_workers > 1` two mutations edited the same directory and wrote to the same submission slot.

Those four things are now four things:

    pantheon.evolution.core.loop.evolve   the loop        (a function, ~220 lines)
    pantheon.evolution.methods            the algorithm   (AgentMapElites, SimpleTES, ...)
    pantheon.evolution.variators          the operator    (AgentVariator, per-call isolated)
    pantheon.evolution.variators.adapters evaluation

This module re-exports the old names so existing callers keep working. New code should build a
method and call `evolve()` directly -- that is the only way to choose an algorithm.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union

from .compat import EvolutionTeam
from .config import EvolutionConfig
from .program import CodebaseSnapshot
from .result import EvolutionResult
from .utils.metrics import compute_fitness_score  # noqa: F401  (historic import path)
from .variators.agent import MUTATION_AGENT_SYSTEM_PROMPT, extract_cost as extract_cost_from_response


def format_metrics_for_log(metrics: Dict[str, Any], max_metrics: int = 3) -> str:
    """Compact one-line metric rendering, kept for callers that logged with it."""
    if not metrics:
        return "no metrics"
    skip = {"fitness_weights", "llm_score", "eval_time"}
    items = [(k, v) for k, v in metrics.items()
             if k not in skip and isinstance(v, (int, float))]
    items.sort(key=lambda kv: (kv[0] != "combined_score", kv[0]))
    return ", ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                     for k, v in items[:max_metrics]) or "no metrics"


def get_primary_metric(metrics: Dict[str, Any]) -> Tuple[str, float]:
    """The metric a human reads first: `combined_score` when present, else the first number."""
    if not metrics:
        return ("none", 0.0)
    if isinstance(metrics.get("combined_score"), (int, float)):
        return ("combined_score", float(metrics["combined_score"]))
    for k, v in metrics.items():
        if k != "fitness_weights" and isinstance(v, (int, float)):
            return (k, float(v))
    return ("none", 0.0)


async def evolve(
    initial_code: Union[str, CodebaseSnapshot],
    evaluator_code: str,
    objective: str,
    config: Optional[EvolutionConfig] = None,
    **kwargs,
) -> EvolutionResult:
    """Run evolution with the default algorithm (MAP-Elites over islands)."""
    return await EvolutionTeam(config=config).evolve(
        initial_code=initial_code,
        evaluator_code=evaluator_code,
        objective=objective,
        **kwargs,
    )


__all__ = [
    "EvolutionTeam",
    "evolve",
    "MUTATION_AGENT_SYSTEM_PROMPT",
    "format_metrics_for_log",
    "get_primary_metric",
    "extract_cost_from_response",
]

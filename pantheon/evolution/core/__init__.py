"""Core abstractions for pluggable evolutionary methods.

`pantheon.evolution` today implements exactly one algorithm -- MAP-Elites over islands with an
LLM mutation operator -- with its parts spread across `EvolutionTeam` and `EvolutionDatabase` and
selected by boolean config flags. This package holds the seams that let a second algorithm exist:
what is evolved (`Genome`), what is stored (`Individual`, `Store`), what work looks like
(`Create`, `Remeasure`, `Measurement`, `Failure`), and who decides (`EvolveMethod`).
"""
from .genome import CodeGenome, Genome, ItemsGenome, TextGenome
from .individual import Individual, Ranking, Store
from .method import (
    BaseMethod,
    Budget,
    EvolveContext,
    EvolveMethod,
    Evaluator,
    Variator,
)
from .work import (
    Create,
    Failure,
    Measurement,
    Produced,
    PromptContext,
    Remeasure,
    WorkItem,
    new_id,
)

__all__ = [
    "Genome", "CodeGenome", "TextGenome", "ItemsGenome",
    "Individual", "Store", "Ranking",
    "Create", "Remeasure", "WorkItem", "Measurement", "Failure", "Produced",
    "PromptContext", "new_id",
    "EvolveMethod", "BaseMethod", "EvolveContext", "Budget", "Variator", "Evaluator",
]

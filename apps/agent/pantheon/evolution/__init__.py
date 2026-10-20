"""
Pantheon Evolution - Evolutionary code optimization framework.

This module provides tools for evolving codebases through iterative
LLM-guided mutations and evaluations, using MAP-Elites for
quality-diversity optimization.

Example usage:
    from pantheon.evolution import EvolutionTeam, EvolutionConfig

    # Create evolution team
    team = EvolutionTeam(
        config=EvolutionConfig(
            max_iterations=100,
            num_islands=3,
        ),
    )

    # Run evolution
    result = await team.evolve(
        initial_code=open("program.py", encoding="utf-8").read(),
        evaluator_code=open("evaluator.py", encoding="utf-8").read(),
        objective="Optimize for speed while maintaining correctness",
    )

    print(f"Best score: {result.best_score}")
    print(f"Best code:\\n{result.best_code}")
"""

from .config import (
    EvolutionConfig,
    get_balanced_config,
    get_fast_config,
    get_thorough_config,
)
from .database import EvolutionDatabase
from .evaluator import (
    EvaluationResult,
    FunctionEvaluator,
    HybridEvaluator,
)
from .program import CodebaseSnapshot, Program
from .prompt_builder import (
    EvolutionPromptBuilder,
    MUTATION_SYSTEM_PROMPT_CODEBASE,
    build_simple_prompt,
)
from .result import EvolutionResult, IterationResult
from .team import EvolutionTeam, evolve
from .visualizer import EvolutionVisualizer, generate_evolution_report

__all__ = [
    # Core classes
    "EvolutionTeam",
    "EvolutionConfig",
    "EvolutionDatabase",
    "EvolutionResult",
    "IterationResult",
    # Program structures
    "Program",
    "CodebaseSnapshot",
    # Evaluation
    "HybridEvaluator",
    "FunctionEvaluator",
    "EvaluationResult",
    # Prompt building
    "EvolutionPromptBuilder",
    "MUTATION_SYSTEM_PROMPT_CODEBASE",
    "build_simple_prompt",
    # Config presets
    "get_fast_config",
    "get_balanced_config",
    "get_thorough_config",
    # Convenience function
    "evolve",
    # Visualization
    "EvolutionVisualizer",
    "generate_evolution_report",
]

__version__ = "0.1.0"

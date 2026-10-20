"""
Evolution configuration system.

Provides EvolutionConfig dataclass with defaults and YAML loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class EvolutionConfig:
    """
    Configuration for evolution process.

    Covers database, evaluation, mutation, and persistence settings.
    """

    # === Evolution Parameters ===
    max_iterations: int = 100
    checkpoint_interval: int = 1  # Save every iteration for real-time UI updates
    early_stop_generations: int = 20  # Stop if no improvement for N generations
    num_workers: int = 1  # Number of parallel workers for evolution

    # === Database Parameters ===
    num_islands: int = 3
    migration_interval: int = 20
    migration_rate: float = 0.1
    feature_dimensions: List[str] = field(
        default_factory=list  # Empty = auto-detect from evaluator metrics
    )
    feature_bins: int = 10
    feature_range_padding: float = 0.1  # Padding on observed min/max (10%)
    feature_range_adaptive: bool = True  # Use adaptive ranges based on observed data
    archive_ratio: float = 0.25  # Top 25% of programs in elite archive
    population_size: int = 500

    # === Sampling Parameters ===
    num_inspirations: int = 2
    num_top_programs: int = 3
    exploration_ratio: float = 0.2  # Random sampling
    exploitation_ratio: float = 0.7  # Elite sampling
    # Remaining (0.1) = fitness-weighted sampling

    # === Evaluation Parameters ===
    evaluation_timeout: int = 120  # seconds
    max_parallel_evaluations: int = 4
    function_weight: float = 0.8  # Weight for function-based evaluation
    llm_weight: float = 0.2  # Weight for LLM feedback
    cascade_evaluation: bool = False
    cascade_thresholds: List[float] = field(
        default_factory=lambda: [0.3, 0.6, 0.8]
    )
    feedback_max_lines_per_file: Optional[int] = None  # Max lines per file for LLM feedback (None = no limit)

    # === Mutation Parameters ===
    diff_based_evolution: bool = True
    max_code_length: int = 50000  # Max total code bytes
    max_diff_size: int = 5000  # Max diff size bytes
    temperature: float = 0.7  # LLM temperature for mutation
    max_retries: int = 3  # Retries for failed mutations
    mutation_timeout: int = 120  # Timeout for LLM mutation call (seconds)
    # Single-agent mutation: replace the analyzer+mutator (+summarizer) pipeline with ONE
    # full-capability coding agent (a one-agent PantheonTeam) that edits a workspace, verifies
    # with run_evaluator, and commits via submit() (result + summary), like a git commit.
    single_agent_mutation: bool = True
    # Sandbox mutation: run each mutation (agent + tools + eval) in an ISOLATED Modal sandbox —
    # no host filesystem access, and trivially parallel (one sandbox per mutation). Overrides
    # single_agent_mutation when True.
    sandbox_mutation: bool = False
    sandbox_image: str = "nanguage/pantheon-agents:latest"
    # Inject MAP-Elites inspirations (elites sampled from OTHER niches) into the mutation as
    # read-only reference code, so the agent can borrow/combine ideas across cells — not just
    # follow its own lineage. Off = classic single-parent mutation. Sandbox path only.
    sandbox_inspirations: bool = False
    # Override the mutation agent's system prompt (persona/task framing). None = the default
    # algorithm-designer prompt. Set this to retarget the same evolution machinery at a
    # non-code genome (e.g. gene-panel design: "you are a panel designer, edit panel.txt").
    mutation_system_prompt: Optional[str] = None
    # Give the mutation agent a web_search tool (DuckDuckGo) so it can research the domain itself
    # (e.g. find marker genes / pathways) instead of being handed the answer. Single-agent path.
    mutation_web_search: bool = False
    # Cap how many times the mutation agent may call run_evaluator per mutation. None = unlimited.
    # Once hit, run_evaluator refuses and tells the agent to submit; the best VERIFIED version seen
    # is auto-submitted if the agent doesn't. Bounds slow mutations without losing exploration.
    # NOTE: this is a SOFT limit — the agent can still self-score in python. For a HARD cap on the
    # agent's total rounds (unbypassable), use max_mutation_turns.
    max_evaluations_per_mutation: Optional[int] = None
    # HARD cap on the number of LLM turns (reasoning+tool rounds) the mutation agent may take, per
    # mutation. None = unlimited. Unlike the eval cap, this counts EVERY turn (python/shell too), so
    # the agent cannot bypass it. The best verified version is auto-committed when the turns run out.
    max_mutation_turns: Optional[int] = None
    # Warm-start: name of a data file (inside the genome) that carries the best solution the code
    # PRODUCED, so the next generation can load and polish it instead of re-deriving it from scratch.
    # When set, the framework persists the evaluator's returned "_state" into this file on each child
    # (after the code diff is computed, so the blob never pollutes the diff). None disables warm-start.
    # Only meaningful when the code RUNS an optimizer to produce a numerical solution (e.g. circle
    # packing); for problems whose artifact IS the genome (e.g. a gene panel) leave it None.
    warm_start_file: Optional[str] = None
    # Use ABSOLUTE fitness instead of min-max normalizing each metric against the archive's observed
    # range. Default False keeps the adaptive normalization (good when metrics live on unknown/wildly
    # different scales). Set True when the evaluator's score is already a meaningful absolute value in
    # ~[0,1+] (e.g. combined_score = sum_radii / target): fitness then equals the raw score, so the
    # champion is NOT pinned to 1.0 and "good vs slightly-better" keeps a real selection gradient —
    # and a score above 1.0 (beating the target) still counts, since absolute fitness is not clamped.
    fitness_absolute: bool = False
    # HARD cap on the number of ACTION tool calls (python/shell/run_evaluator/web_search/file ops —
    # everything EXCEPT submit) the mutation agent may make per mutation. None = unlimited. Every
    # tool result carries a "N actions left" countdown so the agent self-paces; once the budget is
    # spent, further tool calls FAIL with an instruction to submit, while submit() stays available at
    # all times. Preferred over max_mutation_turns: the signal is in-band (a tool failure the agent
    # must react to) and the agent always keeps the ability to finalize its own best work via submit.
    max_tool_calls_per_mutation: Optional[int] = None

    # === Analyzer Parameters ===
    use_analyzer: bool = True  # Enable analyzer agent before mutator
    analyzer_model: str = "normal"
    analyzer_timeout: int = 60  # Timeout for analyzer (seconds)
    top_programs_probability: float = 0.5  # Probability of including top programs in prompt
    inspirations_probability: float = 0.2  # Probability of including inspirations in prompt
    analyzer_use_python: bool = False  # Enable Python interpreter for analyzer
    analyzer_python_workdir: Optional[str] = None  # Working directory for Python interpreter

    # === Analyzer Strategy Parameters ===
    # Controls exploration vs exploitation balance based on generation
    # Exploration: algorithm-level, mathematical, architectural improvements
    # Exploitation: code-level, implementation, fine-grained optimizations
    analyzer_exploration_initial: float = 0.9  # Exploration probability at generation 0
    analyzer_exploration_final: float = 0.1  # Minimum exploration probability
    analyzer_exploration_decay_generations: int = 10  # Generations to decay to final probability

    # === Evolution History Parameters ===
    # Used to construct evolution history for prompts
    summarizer_timeout: int = 30  # Timeout for summarizer agent (seconds)
    evolution_history_max_siblings: int = 5  # Max sibling attempts to show in prompt
    evolution_history_max_ancestors: int = 10  # Max ancestor chain steps to show
    evolution_history_max_chars: int = 2000  # Max total characters for history section

    # === Model Configuration ===
    mutator_model: str = "normal"
    feedback_model: str = "normal"

    # === Persistence ===
    db_path: Optional[str] = None  # Path to save database
    workspace_path: Optional[str] = None  # Path for evaluation workspaces
    save_prompts: bool = True
    save_all_programs: bool = True  # Save all programs for debugging and analysis

    # === Logging ===
    log_level: str = "INFO"
    log_iterations: bool = True
    log_improvements: bool = True

    def validate(self) -> List[str]:
        """
        Validate configuration and return list of warnings.

        Returns:
            List of warning messages (empty if valid)
        """
        warnings = []

        # Check ratios
        total_ratio = self.exploration_ratio + self.exploitation_ratio
        if total_ratio > 1.0:
            warnings.append(
                f"exploration_ratio + exploitation_ratio = {total_ratio} > 1.0"
            )

        # Check weights
        total_weight = self.function_weight + self.llm_weight
        if abs(total_weight - 1.0) > 0.01:
            warnings.append(
                f"function_weight + llm_weight = {total_weight} != 1.0"
            )

        # Check intervals
        if self.checkpoint_interval > self.max_iterations:
            warnings.append(
                "checkpoint_interval > max_iterations, no checkpoints will be saved"
            )

        if self.migration_interval > self.max_iterations:
            warnings.append(
                "migration_interval > max_iterations, no migrations will occur"
            )

        # Check paths
        if self.db_path:
            db_parent = Path(self.db_path).parent
            if not db_parent.exists():
                warnings.append(f"db_path parent directory does not exist: {db_parent}")

        return warnings

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "max_iterations": self.max_iterations,
            "checkpoint_interval": self.checkpoint_interval,
            "early_stop_generations": self.early_stop_generations,
            "num_workers": self.num_workers,
            "num_islands": self.num_islands,
            "migration_interval": self.migration_interval,
            "migration_rate": self.migration_rate,
            "feature_dimensions": self.feature_dimensions,
            "feature_bins": self.feature_bins,
            "feature_range_padding": self.feature_range_padding,
            "feature_range_adaptive": self.feature_range_adaptive,
            "archive_ratio": self.archive_ratio,
            "population_size": self.population_size,
            "num_inspirations": self.num_inspirations,
            "num_top_programs": self.num_top_programs,
            "exploration_ratio": self.exploration_ratio,
            "exploitation_ratio": self.exploitation_ratio,
            "evaluation_timeout": self.evaluation_timeout,
            "max_parallel_evaluations": self.max_parallel_evaluations,
            "function_weight": self.function_weight,
            "llm_weight": self.llm_weight,
            "cascade_evaluation": self.cascade_evaluation,
            "cascade_thresholds": self.cascade_thresholds,
            "feedback_max_lines_per_file": self.feedback_max_lines_per_file,
            "diff_based_evolution": self.diff_based_evolution,
            "max_code_length": self.max_code_length,
            "max_diff_size": self.max_diff_size,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "mutation_timeout": self.mutation_timeout,
            "single_agent_mutation": self.single_agent_mutation,
            "sandbox_mutation": self.sandbox_mutation,
            "sandbox_image": self.sandbox_image,
            "sandbox_inspirations": self.sandbox_inspirations,
            "mutation_system_prompt": self.mutation_system_prompt,
            "mutation_web_search": self.mutation_web_search,
            "max_evaluations_per_mutation": self.max_evaluations_per_mutation,
            "max_mutation_turns": self.max_mutation_turns,
            "max_tool_calls_per_mutation": self.max_tool_calls_per_mutation,
            "fitness_absolute": self.fitness_absolute,
            "warm_start_file": self.warm_start_file,
            "use_analyzer": self.use_analyzer,
            "analyzer_model": self.analyzer_model,
            "analyzer_timeout": self.analyzer_timeout,
            "top_programs_probability": self.top_programs_probability,
            "inspirations_probability": self.inspirations_probability,
            "analyzer_use_python": self.analyzer_use_python,
            "analyzer_python_workdir": self.analyzer_python_workdir,
            "analyzer_exploration_initial": self.analyzer_exploration_initial,
            "analyzer_exploration_final": self.analyzer_exploration_final,
            "analyzer_exploration_decay_generations": self.analyzer_exploration_decay_generations,
            "summarizer_timeout": self.summarizer_timeout,
            "evolution_history_max_siblings": self.evolution_history_max_siblings,
            "evolution_history_max_ancestors": self.evolution_history_max_ancestors,
            "evolution_history_max_chars": self.evolution_history_max_chars,
            "mutator_model": self.mutator_model,
            "feedback_model": self.feedback_model,
            "db_path": self.db_path,
            "workspace_path": self.workspace_path,
            "save_prompts": self.save_prompts,
            "save_all_programs": self.save_all_programs,
            "log_level": self.log_level,
            "log_iterations": self.log_iterations,
            "log_improvements": self.log_improvements,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvolutionConfig":
        """Create from dictionary, ignoring unknown keys."""
        known_fields = {
            "max_iterations",
            "checkpoint_interval",
            "early_stop_generations",
            "num_workers",
            "num_islands",
            "migration_interval",
            "migration_rate",
            "feature_dimensions",
            "feature_bins",
            "feature_range_padding",
            "feature_range_adaptive",
            "archive_ratio",
            "population_size",
            "num_inspirations",
            "num_top_programs",
            "exploration_ratio",
            "exploitation_ratio",
            "evaluation_timeout",
            "max_parallel_evaluations",
            "function_weight",
            "llm_weight",
            "cascade_evaluation",
            "cascade_thresholds",
            "feedback_max_lines_per_file",
            "diff_based_evolution",
            "max_code_length",
            "max_diff_size",
            "temperature",
            "max_retries",
            "mutation_timeout",
            "single_agent_mutation",
            "sandbox_mutation",
            "sandbox_image",
            "sandbox_inspirations",
            "mutation_system_prompt",
            "mutation_web_search",
            "max_evaluations_per_mutation",
            "max_mutation_turns",
            "max_tool_calls_per_mutation",
            "fitness_absolute",
            "warm_start_file",
            "use_analyzer",
            "analyzer_model",
            "analyzer_timeout",
            "top_programs_probability",
            "inspirations_probability",
            "analyzer_use_python",
            "analyzer_python_workdir",
            "analyzer_exploration_initial",
            "analyzer_exploration_final",
            "analyzer_exploration_decay_generations",
            "summarizer_timeout",
            "evolution_history_max_siblings",
            "evolution_history_max_ancestors",
            "evolution_history_max_chars",
            "mutator_model",
            "feedback_model",
            "db_path",
            "workspace_path",
            "save_prompts",
            "save_all_programs",
            "log_level",
            "log_iterations",
            "log_improvements",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def from_yaml(cls, path: str) -> "EvolutionConfig":
        """
        Load configuration from YAML file.

        Args:
            path: Path to YAML file

        Returns:
            EvolutionConfig instance
        """
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})

    def to_yaml(self, path: str) -> None:
        """
        Save configuration to YAML file.

        Args:
            path: Path to save to
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    def with_updates(self, **kwargs) -> "EvolutionConfig":
        """
        Create new config with updates.

        Args:
            **kwargs: Fields to update

        Returns:
            New EvolutionConfig with updates applied
        """
        data = self.to_dict()
        data.update(kwargs)
        return self.from_dict(data)


# Preset configurations for common use cases


def get_fast_config() -> EvolutionConfig:
    """Get configuration optimized for fast iteration."""
    return EvolutionConfig(
        max_iterations=20,
        checkpoint_interval=5,
        num_islands=1,
        max_parallel_evaluations=2,
        evaluation_timeout=30,
        num_inspirations=1,
        num_top_programs=2,
    )


def get_thorough_config() -> EvolutionConfig:
    """Get configuration optimized for thorough exploration."""
    return EvolutionConfig(
        max_iterations=500,
        checkpoint_interval=50,
        num_islands=5,
        migration_interval=50,
        max_parallel_evaluations=8,
        evaluation_timeout=300,
        num_inspirations=3,
        num_top_programs=5,
        archive_ratio=0.2,  # Top 20% for large scale
        population_size=1000,
    )


def get_balanced_config() -> EvolutionConfig:
    """Get balanced configuration (default)."""
    return EvolutionConfig()

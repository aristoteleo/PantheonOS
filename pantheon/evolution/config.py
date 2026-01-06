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
    checkpoint_interval: int = 10
    early_stop_generations: int = 20  # Stop if no improvement for N generations

    # === Database Parameters ===
    num_islands: int = 3
    migration_interval: int = 20
    migration_rate: float = 0.1
    feature_dimensions: List[str] = field(
        default_factory=lambda: ["complexity", "diversity"]
    )
    feature_bins: int = 10
    feature_range_padding: float = 0.1  # Padding on observed min/max (10%)
    feature_range_adaptive: bool = True  # Use adaptive ranges based on observed data
    archive_size: int = 100
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

    # === Model Configuration ===
    mutator_model: str = "normal"
    feedback_model: str = "normal"

    # === Persistence ===
    db_path: Optional[str] = None  # Path to save database
    workspace_path: Optional[str] = None  # Path for evaluation workspaces
    save_prompts: bool = True
    save_all_programs: bool = False  # Save all programs or just archive

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
            "num_islands": self.num_islands,
            "migration_interval": self.migration_interval,
            "migration_rate": self.migration_rate,
            "feature_dimensions": self.feature_dimensions,
            "feature_bins": self.feature_bins,
            "feature_range_padding": self.feature_range_padding,
            "feature_range_adaptive": self.feature_range_adaptive,
            "archive_size": self.archive_size,
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
            "num_islands",
            "migration_interval",
            "migration_rate",
            "feature_dimensions",
            "feature_bins",
            "feature_range_padding",
            "feature_range_adaptive",
            "archive_size",
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
        archive_size=200,
        population_size=1000,
    )


def get_balanced_config() -> EvolutionConfig:
    """Get balanced configuration (default)."""
    return EvolutionConfig()

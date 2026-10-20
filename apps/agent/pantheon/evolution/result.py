"""
Evolution result structures.

Contains EvolutionResult for returning evolution outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .database import EvolutionDatabase
from .program import Program


@dataclass
class IterationResult:
    """Result of a single evolution iteration."""

    iteration: int
    parent_id: str
    child_id: str
    parent_score: float
    child_score: float
    improvement: float
    accepted: bool
    mutation_time: float = 0.0
    evaluation_time: float = 0.0
    total_time: float = 0.0
    error: Optional[str] = None
    # LLM cost tracking
    llm_cost: float = 0.0  # Cost in USD for this iteration


@dataclass
class EvolutionResult:
    """
    Complete result of an evolution run.

    Contains the best program found, history, and statistics.
    """

    # Best program
    best_program: Optional[Program] = None
    best_score: float = 0.0
    best_code: str = ""

    # Run statistics
    total_iterations: int = 0
    successful_iterations: int = 0
    improvements: int = 0
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    end_time: str = ""
    total_duration: float = 0.0

    # LLM cost tracking
    total_cost: float = 0.0  # Total LLM cost in USD

    # Scores over time
    score_history: List[float] = field(default_factory=list)
    best_score_history: List[float] = field(default_factory=list)

    # Iteration details
    iteration_results: List[IterationResult] = field(default_factory=list)

    # Database reference (optional, for continued evolution)
    database: Optional[EvolutionDatabase] = None

    # Configuration used
    config_used: Dict[str, Any] = field(default_factory=dict)

    # Errors encountered
    errors: List[str] = field(default_factory=list)

    def finalize(self) -> None:
        """Finalize the result after evolution completes."""
        self.end_time = datetime.now().isoformat()

        if self.best_program:
            self.best_code = self.best_program.get_combined_code()

        # Calculate successful iterations
        self.successful_iterations = sum(
            1 for r in self.iteration_results if r.error is None
        )

        # Calculate improvements
        self.improvements = sum(
            1 for r in self.iteration_results if r.improvement > 0
        )

        # Calculate total LLM cost
        self.total_cost = sum(r.llm_cost for r in self.iteration_results)

    def get_improvement_rate(self) -> float:
        """Get the rate of successful improvements."""
        if not self.iteration_results:
            return 0.0
        return self.improvements / len(self.iteration_results)

    def get_success_rate(self) -> float:
        """Get the rate of successful evaluations."""
        if not self.iteration_results:
            return 0.0
        return self.successful_iterations / len(self.iteration_results)

    def get_avg_iteration_time(self) -> float:
        """Get average time per iteration."""
        if not self.iteration_results:
            return 0.0
        total = sum(r.total_time for r in self.iteration_results)
        return total / len(self.iteration_results)

    def _format_metrics(self, metrics: Dict[str, Any], max_metrics: int = 3) -> str:
        """Format raw metrics for display."""
        if not metrics:
            return "no_metrics"
        fitness_weights = metrics.get("fitness_weights", {})
        priority_metrics = []
        other_metrics = []
        for key, value in metrics.items():
            if key in ("fitness_weights", "error", "llm_feedback"):
                continue
            if not isinstance(value, (int, float)):
                continue
            if key in fitness_weights:
                priority_metrics.append((key, value))
            else:
                other_metrics.append((key, value))
        all_metrics = priority_metrics + other_metrics
        selected = all_metrics[:max_metrics]
        if not selected:
            return "no_metrics"
        return " ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                        for k, v in selected)

    def get_summary(self) -> str:
        """Get a human-readable summary."""
        # Get raw metrics from best program if available
        best_metrics_str = ""
        if self.best_program and self.best_program.metrics:
            best_metrics_str = self._format_metrics(self.best_program.metrics)

        lines = [
            "=" * 50,
            "Evolution Results Summary",
            "=" * 50,
            f"Total iterations: {self.total_iterations}",
            f"Successful: {self.successful_iterations} ({self.get_success_rate()*100:.1f}%)",
            f"Improvements: {self.improvements} ({self.get_improvement_rate()*100:.1f}%)",
            f"",
            f"Best metrics: {best_metrics_str}" if best_metrics_str else f"Best score: {self.best_score:.4f}",
            f"Best fitness_score (normalized): {self.best_score:.4f}",
            f"",
            f"Duration: {self.total_duration:.1f}s",
            f"Avg time/iteration: {self.get_avg_iteration_time():.2f}s",
            f"",
            f"LLM Cost: ${self.total_cost:.4f}",
            f"Avg cost/iteration: ${self.total_cost / max(self.total_iterations, 1):.4f}",
            "=" * 50,
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "best_score": self.best_score,
            "best_code": self.best_code,
            "total_iterations": self.total_iterations,
            "successful_iterations": self.successful_iterations,
            "improvements": self.improvements,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_duration": self.total_duration,
            "total_cost": self.total_cost,
            "avg_cost_per_iteration": self.total_cost / max(self.total_iterations, 1),
            "score_history": self.score_history,
            "best_score_history": self.best_score_history,
            "improvement_rate": self.get_improvement_rate(),
            "success_rate": self.get_success_rate(),
            "config_used": self.config_used,
            "errors": self.errors,
        }

    def save_report(self, path: str) -> None:
        """Save detailed report to file."""
        import json
        from pathlib import Path

        report_path = Path(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "summary": self.get_summary(),
            "data": self.to_dict(),
            "iteration_details": [
                {
                    "iteration": r.iteration,
                    "parent_score": r.parent_score,
                    "child_score": r.child_score,
                    "improvement": r.improvement,
                    "accepted": r.accepted,
                    "error": r.error,
                    "llm_cost": r.llm_cost,
                }
                for r in self.iteration_results
            ],
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    def save_html_report(self, path: str) -> str:
        """
        Generate and save an HTML visualization report.

        Args:
            path: Path to save the HTML file

        Returns:
            Path to the generated HTML file

        Raises:
            ValueError: If database is not available
        """
        from .visualizer import EvolutionVisualizer

        if self.database is None:
            raise ValueError(
                "Cannot generate HTML report: database not available. "
                "Make sure to save the evolution results first."
            )

        visualizer = EvolutionVisualizer(self.database)
        return visualizer.generate_html(path)

"""
Evolution Visualizer - Generate HTML visualization of evolution results.

Creates interactive HTML reports showing:
- Evolution tree (parent-child relationships)
- Score history charts
- Diff viewer for each mutation
- LLM feedback and metrics
- MAP-Elites heatmap
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import EvolutionDatabase
from .program import Program


@dataclass
class TreeNode:
    """Node in the evolution tree."""
    id: str
    parent_id: Optional[str]
    children: List["TreeNode"]
    generation: int
    island_id: int
    score: float
    metrics: Dict[str, float]
    diff: str
    llm_feedback: str
    created_at: str
    is_best: bool = False


class EvolutionVisualizer:
    """
    Generate HTML visualization reports for evolution results.

    Usage:
        visualizer = EvolutionVisualizer.from_path("evolution_results/")
        visualizer.generate_html("report.html")
    """

    def __init__(self, database: EvolutionDatabase, objective: str = ""):
        """
        Initialize visualizer with a loaded database.

        Args:
            database: Loaded EvolutionDatabase
            objective: Optimization objective description
        """
        self.database = database
        self.programs = database.programs
        self.objective = objective
        self.metadata = {
            "config": database.config.to_dict(),
            "best_program_id": database.best_program_id,
            "archive": list(database.archive),
            "total_added": database.total_added,
            "total_improved": database.total_improved,
        }

    @classmethod
    def from_path(cls, db_path: str) -> "EvolutionVisualizer":
        """
        Create visualizer from saved evolution results.

        Args:
            db_path: Path to evolution_results directory

        Returns:
            EvolutionVisualizer instance
        """
        database = EvolutionDatabase.load(db_path)

        # Load objective from evolution_state.json if available
        objective = ""
        state_path = Path(db_path) / "evolution_state.json"
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    objective = state.get("objective", "")
            except Exception:
                pass

        return cls(database, objective=objective)

    def build_tree_data(self) -> Dict[str, Any]:
        """
        Build tree structure for D3.js visualization.

        Returns:
            Dict representing tree with format:
            {
                "id": "root",
                "name": "Initial",
                "children": [...],
                "score": 0.5,
                ...
            }
        """
        # Find root programs (no parent)
        roots = []
        children_map: Dict[str, List[str]] = {}

        for prog_id, prog in self.programs.items():
            if prog.parent_id is None:
                roots.append(prog_id)
            else:
                if prog.parent_id not in children_map:
                    children_map[prog.parent_id] = []
                children_map[prog.parent_id].append(prog_id)

        def build_node(prog_id: str) -> Dict[str, Any]:
            prog = self.programs[prog_id]

            # Check for evaluation error
            has_error = bool(prog.artifacts.get("evaluation_error"))

            # Use dynamic fitness_score calculation with current metric_ranges
            score = prog.fitness_score(
                self.database.config.feature_dimensions,
                self.database.metric_ranges,
                self.database.config.function_weight,
                self.database.config.llm_weight,
            )

            # Build display_metrics with dynamically computed function_score and fitness_score
            display_metrics = {k: v for k, v in prog.metrics.items() if k != "fitness_weights"}
            fitness_weights = prog.metrics.get("fitness_weights")
            if fitness_weights and isinstance(fitness_weights, dict):
                display_metrics["function_score"] = self.database.compute_function_score(
                    prog.metrics, fitness_weights
                )
            # Add fitness_score to display metrics
            display_metrics["fitness_score"] = score

            # Use program.order if available, otherwise fall back to -1
            order = prog.order if prog.order is not None else -1

            node = {
                "id": prog_id,
                "name": prog_id[:8],
                "order": order,
                "iteration": order,  # Keep 'iteration' for backward compatibility
                "generation": prog.generation,
                "island_id": prog.island_id,
                "score": score,
                "metrics": display_metrics,
                "has_error": has_error,
                "diff": prog.diff_from_parent or "",
                "llm_feedback": prog.llm_feedback or prog.artifacts.get("llm_feedback", ""),
                "created_at": prog.created_at,
                "is_best": prog_id == self.database.best_program_id,
                "children": [],
            }

            # Recursively build children
            if prog_id in children_map:
                for child_id in children_map[prog_id]:
                    node["children"].append(build_node(child_id))

            return node

        # If multiple roots, create a virtual root
        if len(roots) == 0:
            return {"id": "empty", "name": "Empty", "iteration": -1, "children": [], "score": 0}
        elif len(roots) == 1:
            return build_node(roots[0])
        else:
            # Multiple roots - create virtual parent
            return {
                "id": "root",
                "name": "Evolution",
                "iteration": -1,
                "generation": -1,
                "island_id": -1,
                "score": 0,
                "metrics": {},
                "diff": "",
                "llm_feedback": "",
                "created_at": "",
                "is_best": False,
                "children": [build_node(root_id) for root_id in roots],
            }

    def get_score_history(self) -> List[Dict[str, Any]]:
        """
        Get score history sorted by program order.

        Returns:
            List of {iteration, order, program_id, <metric_name>: value, best_<metric_name>: value, ...}
        """
        # Collect all metric keys from all programs (excluding fitness_weights)
        all_metric_keys = set()
        for prog in self.programs.values():
            for k in prog.metrics.keys():
                if k != "fitness_weights":
                    all_metric_keys.add(k)

        # Sort programs by order (use order if available, otherwise fall back to created_at)
        sorted_programs = sorted(
            self.programs.values(),
            key=lambda p: p.order if p.order is not None else float('inf')
        )

        # Get metric_ranges for dynamic function_score calculation
        metric_ranges = self.database.metric_ranges if hasattr(self.database, 'metric_ranges') else {}

        history = []
        best_scores: Dict[str, float] = {}  # Track best value for each metric

        for prog in sorted_programs:
            order = prog.order if prog.order is not None else -1
            entry = {
                "order": order,
                "iteration": order,  # Keep 'iteration' for backward compatibility
                "program_id": prog.id,
            }

            # Dynamically compute function_score using current metric_ranges
            fitness_weights = prog.metrics.get("fitness_weights")
            if fitness_weights and isinstance(fitness_weights, dict):
                function_score = self.database.compute_function_score(
                    prog.metrics, fitness_weights
                )
                entry["function_score"] = function_score
                # Update best function_score
                if "function_score" not in best_scores or function_score > best_scores["function_score"]:
                    best_scores["function_score"] = function_score
                entry["best_function_score"] = best_scores.get("function_score", 0.0)

            # Dynamically compute fitness_score (weighted combination of function_score and llm_score)
            fitness_score = prog.fitness_score(
                self.database.config.feature_dimensions,
                metric_ranges,
                self.database.config.function_weight,
                self.database.config.llm_weight,
            )
            entry["fitness_score"] = fitness_score
            # Update best fitness_score
            if "fitness_score" not in best_scores or fitness_score > best_scores["fitness_score"]:
                best_scores["fitness_score"] = fitness_score
            entry["best_fitness_score"] = best_scores.get("fitness_score", 0.0)

            # Add all other metrics and their best values
            for key in all_metric_keys:
                if key == "function_score":
                    continue  # Already handled above
                value = prog.metrics.get(key, 0.0)
                if not isinstance(value, (int, float)):
                    continue
                entry[key] = value

                # Update best score for this metric
                if key not in best_scores or value > best_scores[key]:
                    best_scores[key] = value
                entry[f"best_{key}"] = best_scores.get(key, 0.0)

            history.append(entry)

        return history

    def get_metric_keys(self) -> List[str]:
        """
        Get all unique metric keys from programs.

        Returns:
            Sorted list of metric key names
        """
        all_metric_keys = set()
        for prog in self.programs.values():
            all_metric_keys.update(prog.metrics.keys())
        # Add dynamically computed scores
        all_metric_keys.add("fitness_score")
        all_metric_keys.add("best_fitness_score")
        return sorted(all_metric_keys)

    def get_programs_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all programs data for detail view.

        Returns:
            Dict mapping program_id to program details
        """
        programs_data = {}

        for prog_id, prog in self.programs.items():
            order = prog.order if prog.order is not None else -1

            # Compute fitness_delta dynamically using current metric_ranges
            prog_fitness = prog.fitness_score(
                self.database.config.feature_dimensions,
                self.database.metric_ranges,
                self.database.config.function_weight,
                self.database.config.llm_weight,
            )
            if prog.parent_id and prog.parent_id in self.programs:
                parent = self.programs[prog.parent_id]
                parent_fitness = parent.fitness_score(
                    self.database.config.feature_dimensions,
                    self.database.metric_ranges,
                    self.database.config.function_weight,
                    self.database.config.llm_weight,
                )
                fitness_delta = prog_fitness - parent_fitness

                # Compute metrics_delta dynamically
                metrics_delta = {}
                for k, v in prog.metrics.items():
                    if k in parent.metrics and isinstance(v, (int, float)):
                        metrics_delta[k] = v - parent.metrics[k]
            else:
                fitness_delta = None
                metrics_delta = {}

            # Build metrics with dynamically computed function_score and fitness_score
            display_metrics = {k: v for k, v in prog.metrics.items() if k != "fitness_weights"}
            fitness_weights = prog.metrics.get("fitness_weights")
            if fitness_weights and isinstance(fitness_weights, dict):
                display_metrics["function_score"] = self.database.compute_function_score(
                    prog.metrics, fitness_weights
                )
            # Add fitness_score to display metrics
            display_metrics["fitness_score"] = prog_fitness

            programs_data[prog_id] = {
                "id": prog_id,
                "order": order,
                "parent_id": prog.parent_id,
                "generation": prog.generation,
                "island_id": prog.island_id,
                "metrics": display_metrics,
                "diff": prog.diff_from_parent or "",
                "llm_feedback": prog.llm_feedback or prog.artifacts.get("llm_feedback", ""),
                "issues": prog.artifacts.get("issues", []),
                "suggestions": prog.artifacts.get("suggestions", []),
                "created_at": prog.created_at,
                "is_best": prog_id == self.database.best_program_id,
                "code_files": self._get_code_files(prog),
                "analysis_used": prog.analysis_used or "",
                "analysis_prompt_used": prog.analysis_prompt_used or "",
                "mutator_prompt_used": prog.mutator_prompt_used or "",
                # Mutation summary fields
                "mutation_summary": prog.mutation_summary,
                "mutation_category": prog.mutation_category,
                "is_algorithmic": prog.is_algorithmic,
                "fitness_delta": fitness_delta,
                "metrics_delta": metrics_delta,
            }

        return programs_data

    def _get_code_files(self, prog: Program) -> Dict[str, str]:
        """Get program code as a dict of {filepath: content}."""
        if prog.snapshot and prog.snapshot.files:
            return dict(prog.snapshot.files)
        return {}

    def get_map_elites_data(self) -> List[Dict[str, Any]]:
        """
        Get MAP-Elites grid data for heatmap visualization.

        Uses dynamic bin calculation based on current feature ranges.

        Returns:
            List of {coords, score, program_id, metrics, ...} for each filled cell
        """
        cells = []
        feature_dims = self.metadata.get("config", {}).get("feature_dimensions", [])
        num_islands = self.database.config.num_islands

        for island_id in range(num_islands):
            # Use iter_filled_bins for dynamic bin calculation
            for coords, prog_id in self.database.iter_filled_bins(island_id):
                if prog_id in self.programs:
                    prog = self.programs[prog_id]
                    # Use dynamic fitness_score calculation
                    score = prog.fitness_score(
                        self.database.config.feature_dimensions,
                        self.database.metric_ranges,
                        self.database.config.function_weight,
                        self.database.config.llm_weight,
                    )

                    # Store full coordinates as dict mapping dimension name to bin index
                    coords_dict = {}
                    for i, dim in enumerate(feature_dims):
                        if i < len(coords):
                            coords_dict[dim] = coords[i]

                    # Build display metrics: filter out fitness_weights, add fitness_score
                    display_metrics = {k: v for k, v in prog.metrics.items() if k != "fitness_weights"}
                    display_metrics["fitness_score"] = score

                    cells.append({
                        "coords": coords_dict,  # Full coordinates by dimension name
                        "score": score,
                        "program_id": prog_id,
                        "island_id": island_id,
                        "metrics": display_metrics,
                        "generation": prog.generation,
                    })

        return cells

    def get_summary_stats(self) -> Dict[str, Any]:
        """Get summary statistics for the evolution run."""
        stats = self.database.get_statistics()

        # Calculate additional stats using dynamic fitness_score
        scores = [
            p.fitness_score(
                self.database.config.feature_dimensions,
                self.database.metric_ranges,
                self.database.config.function_weight,
                self.database.config.llm_weight,
            )
            for p in self.programs.values()
        ]

        best_prog = self.database.get_best_program()
        initial_score = 0.0

        # Find initial program (generation 0)
        for prog in self.programs.values():
            if prog.generation == 0:
                initial_score = prog.fitness_score(
                    self.database.config.feature_dimensions,
                    self.database.metric_ranges,
                    self.database.config.function_weight,
                    self.database.config.llm_weight,
                )
                break

        # Get effective feature ranges from database
        feature_ranges = {}
        for dim in self.metadata.get("config", {}).get("feature_dimensions", []):
            feature_ranges[dim] = self.database.get_feature_range(dim)

        return {
            "total_programs": len(self.programs),
            "total_iterations": self.metadata.get("total_added", 0),
            "improvements": self.metadata.get("total_improved", 0),
            "best_score": max(scores) if scores else 0.0,
            "initial_score": initial_score,
            "improvement_pct": ((max(scores) - initial_score) / initial_score * 100) if initial_score > 0 else 0.0,
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
            "num_islands": stats.get("num_islands", 1),
            "archive_size": stats.get("archive_size", 0),
            "feature_dimensions": self.metadata.get("config", {}).get("feature_dimensions", []),
            "feature_ranges": feature_ranges,
            "config": self.metadata.get("config", {}),
        }

    def generate_html(self, output_path: str) -> str:
        """
        Generate complete HTML visualization report.

        Args:
            output_path: Path to save HTML file

        Returns:
            Path to generated HTML file
        """
        # Collect all data
        tree_data = self.build_tree_data()
        score_history = self.get_score_history()
        programs_data = self.get_programs_data()
        map_elites_data = self.get_map_elites_data()
        summary_stats = self.get_summary_stats()
        metric_keys = self.get_metric_keys()

        # Generate HTML
        html_content = self._render_html(
            tree_data=tree_data,
            score_history=score_history,
            programs_data=programs_data,
            map_elites_data=map_elites_data,
            summary_stats=summary_stats,
            metric_keys=metric_keys,
        )

        # Write to file
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_content, encoding="utf-8")

        return str(output_path)

    def _render_html(
        self,
        tree_data: Dict[str, Any],
        score_history: List[Dict[str, Any]],
        programs_data: Dict[str, Dict[str, Any]],
        map_elites_data: List[Dict[str, Any]],
        summary_stats: Dict[str, Any],
        metric_keys: List[str],
    ) -> str:
        """Render the complete HTML report."""

        def sanitize_for_json(obj):
            """Replace NaN and Inf with None for valid JSON serialization."""
            import math
            if isinstance(obj, dict):
                return {k: sanitize_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_for_json(item) for item in obj]
            elif isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            return obj

        # Convert data to JSON for embedding (sanitize NaN/Inf first)
        tree_json = json.dumps(sanitize_for_json(tree_data), ensure_ascii=False)
        history_json = json.dumps(sanitize_for_json(score_history), ensure_ascii=False)
        programs_json = json.dumps(sanitize_for_json(programs_data), ensure_ascii=False)
        map_elites_json = json.dumps(sanitize_for_json(map_elites_data), ensure_ascii=False)
        stats_json = json.dumps(sanitize_for_json(summary_stats), ensure_ascii=False)
        metric_keys_json = json.dumps(metric_keys, ensure_ascii=False)
        objective_json = json.dumps(self.objective, ensure_ascii=False)

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Evolution Report - Pantheon</title>
    <link rel="icon" href="data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4NCjwhLS0gR2VuZXJhdG9yOiBBZG9iZSBJbGx1c3RyYXRvciAyNS4wLjAsIFNWRyBFeHBvcnQgUGx1Zy1JbiAuIFNWRyBWZXJzaW9uOiA2LjAwIEJ1aWxkIDApICAtLT4NCjxzdmcgdmVyc2lvbj0iMS4xIiBpZD0i5Zu+5bGCXzEiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgeG1sbnM6eGxpbms9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkveGxpbmsiIHg9IjBweCIgeT0iMHB4Ig0KCSB2aWV3Qm94PSIwIDAgMTEwLjY0IDExMS4wMiIgc3R5bGU9ImVuYWJsZS1iYWNrZ3JvdW5kOm5ldyAwIDAgMTEwLjY0IDExMS4wMjsiIHhtbDpzcGFjZT0icHJlc2VydmUiPg0KPHN0eWxlIHR5cGU9InRleHQvY3NzIj4NCgkuc3Qwe2ZpbGw6IzU3NDJBMDt9DQoJLnN0MXtmaWxsOiNGRUZFRkU7fQ0KPC9zdHlsZT4NCjxwYXRoIGNsYXNzPSJzdDAiIGQ9Ik04MC4wNSwxMTAuMzdIMzAuNjljLTE2LjY3LDAtMzAuMTctMTMuNTEtMzAuMTctMzAuMTdWMzAuODNjMC0xNi42NywxMy41MS0zMC4xNywzMC4xNy0zMC4xN2g0OS4zNg0KCWMxNi42NywwLDMwLjE3LDEzLjUxLDMwLjE3LDMwLjE3djQ5LjM2QzExMC4yMiw5Ni44Niw5Ni43MSwxMTAuMzcsODAuMDUsMTEwLjM3eiIvPg0KPHBhdGggY2xhc3M9InN0MSIgZD0iTTkyLjUzLDQyLjA1Yy0wLjE1LDAuMTUtMC4zMSwwLjMtMC40NiwwLjQ1Yy0wLjE2LDAuMDgtMC4zMywwLjE3LTAuNDksMC4yNWMwLDAsMC4wMS0wLjAxLDAuMDEtMC4wMQ0KCWMtMC4xOCwwLjAxLTAuMzYsMC4wNi0wLjUzLDAuMDNjLTAuNTgtMC4xLTAuNjYsMC4xOC0wLjY2LDAuNjljMC4wMiwyLjY2LDAuMDEsNS4zMywwLjAxLDcuOTljMCw3Ljg0LDAsMTUuNjctMC4wMSwyMy41MQ0KCWMwLDAuNDQsMC4xLDAuNTYsMC41NSwwLjU2YzAuOTMtMC4wMSwxLjg3LTAuMTIsMi43OCwwLjE0YzAsMC0wLjAyLTAuMDEtMC4wMi0wLjAxYzAuMTMsMC4wOCwwLjI1LDAuMTYsMC4zOCwwLjI0DQoJYzAuMTEsMC4xMSwwLjIyLDAuMjMsMC4zMywwLjM0YzAuMTIsMC4yNCwwLjI0LDAuNDksMC4zNiwwLjczYzAsMCwwLjAyLTAuMDMsMC4wMi0wLjAzYy0wLjAxLDAuNzMsMC4wMSwxLjQ2LTAuMDMsMi4xOA0KCWMtMC4wMiwwLjM3LDAuMSwwLjUsMC40NiwwLjQ0YzAuMTQtMC4wMiwwLjI4LTAuMDEsMC40MS0wLjAxYzAuNjQsMCwxLjI4LDAsMS45MywwYzAuMTIsMC4wMywwLjIzLDAuMDYsMC4zNSwwLjA5DQoJYzAuNjEsMC4yNywxLjAxLDAuNzMsMS4yMiwxLjM2YzAsMS40MywwLjAxLDIuODYsMC4wMSw0LjI5Yy0wLjE2LDAuNy0wLjU4LDEuMi0xLjI0LDEuNDhjLTAuNDMsMC4xNC0wLjg3LDAuMDgtMS4zLDAuMDgNCgljLTI3LjQzLDAtNTQuODYsMC04Mi4yOSwwYy0wLjUsMC0wLjk5LTAuMDItMS40OS0wLjA0Yy0wLjQyLTAuMy0wLjg1LTAuNTgtMS4xLTEuMDVjLTAuMDYtMC4xOS0wLjExLTAuMzktMC4xNy0wLjU4DQoJYzAtMS4yNCwwLTIuNDcsMC0zLjcxYzAuMDEtMC4xMSwwLjAyLTAuMjIsMC4wMi0wLjMzYzAuMjgtMC43NCwwLjc4LTEuMjYsMS41NS0xLjVjMC4xNi0wLjAzLDAuMzEtMC4wNSwwLjQ3LTAuMDgNCgljMC41NiwwLDEuMTIsMCwxLjY4LDBjMCwwLDAuMDYsMC4wMSwwLjA2LDAuMDFzMC4wNi0wLjAxLDAuMDYtMC4wMWMwLjE2LDAsMC4zMiwwLDAuNDgsMGwwLjA2LDAuMDFMMTYsNzkuNTUNCgljMC4xNy0wLjAyLDAuMTItMC4xMywwLjA5LTAuMjNjMC0wLjcxLDAtMS40MywwLjAxLTIuMTRjMC4xNC0wLjgsMC41OS0xLjMzLDEuMzQtMS42YzAuMTYtMC4wMywwLjMxLTAuMDYsMC40Ny0wLjA4DQoJYzAuMDgsMCwwLjE2LDAsMC4yNSwwYzAuNjgsMCwxLjM1LTAuMDEsMi4wMy0wLjAxYzAuMjktMC4xMiwwLjE0LTAuMzgsMC4xNC0wLjU2YzAuMDEtMTAuMjksMC4wMS0yMC41OCwwLjAxLTMwLjg2DQoJYzAtMS4xMywwLTEuMTMtMS4xLTEuMTdjLTAuMTYtMC4wOC0wLjMxLTAuMTYtMC40Ny0wLjI0YzAsMCwwLjAxLDAuMDEsMC4wMSwwLjAxYy0wLjA3LTAuMDUtMC4xNS0wLjExLTAuMjItMC4xNg0KCWMwLDAsMC4wMSwwLjAxLDAuMDEsMC4wMmMtMC4wNC0wLjA0LTAuMDktMC4wOS0wLjEzLTAuMTNjMCwwLDAuMDIsMC4wMSwwLjAyLDAuMDFjLTAuMDUtMC4wNy0wLjExLTAuMTUtMC4xNi0wLjIyDQoJYzAsMCwwLjAxLDAuMDEsMC4wMSwwLjAxYy0wLjItMC4zOC0wLjI3LTAuNzctMC4yNS0xLjJjMC4wMi0wLjYxLTAuMDEtMS4yMywwLjAxLTEuODRjMC4wMS0wLjMxLTAuMDgtMC40MS0wLjQtMC40DQoJYy0wLjY1LDAuMDItMS4zMSwwLTEuOTYsMC4wMWMtMC40NiwwLjAxLTAuOTEtMC4wNC0xLjMtMC4zMmMwLDAtMC4wMiwwLjAxLTAuMDIsMC4wMWMtMC4wNS0wLjA0LTAuMDktMC4wOC0wLjE0LTAuMTENCgljMCwwLDAuMDEsMC4wMSwwLjAxLDAuMDFjLTAuMDQtMC4wNS0wLjA4LTAuMDktMC4xMy0wLjE0YzAsMCwwLjAyLDAuMDEsMC4wMiwwLjAxYy0wLjA1LTAuMDctMC4xMS0wLjE1LTAuMTYtMC4yMg0KCWMwLDAsMC4wMSwwLjAxLDAuMDEsMC4wMWMtMC4wOC0wLjE0LTAuMTYtMC4yOS0wLjIzLTAuNDNjMCwwLDAuMDEtMC4wMSwwLjAxLTAuMDFjMC0wLjY5LTAuMDEtMS4zNy0wLjAxLTIuMDYNCgljMCwwLTAuMDEsMC4wMi0wLjAxLDAuMDJjMC4xMS0wLjA1LDAuMS0wLjE2LDAuMTEtMC4yNmMwLDAsMCwwLjAxLDAsMC4wMWMwLjA1LTAuMDgsMC4wOS0wLjE2LDAuMTQtMC4yNA0KCWMwLjE1LTAuMTUsMC4yOS0wLjMsMC40NC0wLjQ1YzAuNjktMC4zNiwxLjM4LTAuNzIsMi4wNy0xLjA3YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMTMtMC4wMiwwLjI2LTAuMDQsMC4zNS0wLjE2DQoJYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDctMC4wMSwwLjE0LTAuMDEsMC4xNS0wLjExDQoJYzAsMC0wLjAxLDAuMDItMC4wMSwwLjAyYzAuMTMtMC4wMiwwLjI2LTAuMDQsMC4zNS0wLjE2YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjYtMC4xMw0KCWMwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjEtMC4wMiwwLjItMC4wMywwLjI1LTAuMTNjMCwwLTAuMDIsMC4wMS0wLjAxLDAuMDFjMC4xLTAuMDIsMC4yLTAuMDMsMC4yNS0wLjEzDQoJYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDQtMC4wNSwwLjA5LTAuMDksMC4xMy0wLjE0YzAuMDMsMC4wMSwwLjA3LDAsMC4xLTAuMDFjMC4xMSwwLjAzLDAuMiwwLjAxLDAuMjYtMC4xDQoJYzAsMC0wLjAyLDAuMDItMC4wMiwwLjAyYzAuMS0wLjAyLDAuMjEtMC4wMiwwLjI2LTAuMTNjMCwwLTAuMDEsMC4wMi0wLjAxLDAuMDJjMC4zNS0wLjEyLDAuNjktMC4yNSwwLjk3LTAuNDkNCgljMCwwLTAuMDEsMC4wMi0wLjAxLDAuMDJjMC4yNy0wLjA3LDAuNTItMC4xOCwwLjczLTAuMzdjMCwwLTAuMDEsMC4wMi0wLjAxLDAuMDJjMC4xLTAuMDEsMC4yLTAuMDIsMC4yNS0wLjE0YzAsMCwwLDAuMDIsMCwwLjAyDQoJYzAuMDctMC4wMiwwLjE0LTAuMDUsMC4yLTAuMDdjMC4wNC0wLjAzLDAuMDktMC4wNSwwLjEzLTAuMDhjMC4wOC0wLjA0LDAuMTctMC4wOSwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4wOC0wLjA0LDAuMTctMC4wOSwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4xLTAuMDEsMC4yLTAuMDMsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjYtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjEtMC4wMiwwLjItMC4wMywwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4xLTAuMDIsMC4yLTAuMDMsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMS0wLjAyLDAuMjEtMC4wMiwwLjI2LTAuMTNjMCwwLTAuMDIsMC4wMS0wLjAyLDAuMDFjMC4xLTAuMDIsMC4yLTAuMDIsMC4yNi0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzEuMDItMC40MywyLjAxLTAuOTQsMi45OC0xLjQ2YzAsMC0wLjAxLDAtMC4wMSwwYzAuMTYtMC4wOCwwLjMyLTAuMTYsMC40OC0wLjI0YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEyYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDctMC4wMSwwLjE0LTAuMDEsMC4xNS0wLjExYzAsMC0wLjAxLDAuMDItMC4wMSwwLjAyYzAuMTMtMC4wMiwwLjI2LTAuMDQsMC4zNS0wLjE2YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMS0wLjAxLDAuMi0wLjAzLDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjEtMC4wMiwwLjItMC4wMiwwLjI2LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4xLTAuMDIsMC4yLTAuMDMsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjYtMC4xM2MwLDAtMC4wMiwwLjAxLTAuMDIsMC4wMQ0KCWMwLjEtMC4wMiwwLjItMC4wMiwwLjI2LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMS4yNi0wLjU1LDIuNDktMS4xNywzLjctMS44MmMwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjA4LTAuMDQsMC4xNy0wLjA4LDAuMjUtMC4xMmMwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjA4LTAuMDQsMC4xNy0wLjA5LDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjA4LTAuMDQsMC4xNy0wLjA4LDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjA4LTAuMDQsMC4xNy0wLjA4LDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjA4LTAuMDQsMC4xNy0wLjA4LDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjA4LTAuMDQsMC4xNy0wLjA5LDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjA0LDAsMC4wNy0wLjAxLDAuMTEtMC4wM2MwLjkzLTAuMzMsMS43MS0wLjk2LDIuNjMtMS4zMWMwLjQzLTAuMTQsMC44Mi0wLjM2LDEuMi0wLjU4YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEyYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMTYtMC4wOCwwLjMyLTAuMTYsMC40OS0wLjI0YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDgsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMDctMC4wMSwwLjE0LTAuMDEsMC4xNS0wLjExYzAsMCwwLDAuMDIsMCwwLjAyYzAuMTMtMC4wMiwwLjI2LTAuMDQsMC4zNS0wLjE2YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjYtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMWMwLjEtMC4wMiwwLjItMC4wMywwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4xLTAuMDIsMC4yLTAuMDIsMC4yNi0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuNS0wLjIsMS4wMS0wLjQsMS40NC0wLjc0YzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuNC0wLjA4LDAuNzQtMC4yOSwxLjA4LTAuNDljMCwwLTAuMDEsMC0wLjAxLDBjMC4xNi0wLjA4LDAuMzItMC4xNiwwLjQ5LTAuMjRjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTJjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTJjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4wOC0wLjA0LDAuMTctMC4wOSwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4xLTAuMDEsMC4yLTAuMDMsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjUtMC4xM2MwLDAtMC4wMSwwLjAxLTAuMDEsMC4wMQ0KCWMwLjEtMC4wMiwwLjItMC4wMywwLjI1LTAuMTNjMCwwLTAuMDIsMC4wMS0wLjAxLDAuMDFjMC4xLTAuMDIsMC4yLTAuMDIsMC4yNi0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuNy0wLjI4LDEuMzgtMC42MSwyLjAzLTAuOThjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDFjMC4wOC0wLjA0LDAuMTctMC4wOCwwLjI1LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4xLTAuMDEsMC4yLTAuMDMsMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxYzAuMDgtMC4wNCwwLjE3LTAuMDksMC4yNS0wLjEzYzAsMC0wLjAxLDAuMDEtMC4wMSwwLjAxDQoJYzAuMS0wLjAyLDAuMi0wLjAyLDAuMjYtMC4xM2MwLDAtMC4wMiwwLjAxLTAuMDEsMC4wMWMwLjEtMC4wMiwwLjItMC4wMiwwLjI2LTAuMTNjMCwwLTAuMDEsMC4wMS0wLjAxLDAuMDENCgljMC4zNi0wLjEyLDAuNy0wLjI3LDEtMC41YzAuNzgtMC4yMywxLjUtMC42MSwyLjItMWMxLjA5LTAuNiwyLjE0LTAuNzQsMy4yNC0wLjFjMC4wMywwLjAyLDAuMDcsMC4wMywwLjExLDAuMDMNCgljMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wOSwwLjEyLDAuMjIsMC4xNCwwLjM1LDAuMTZjMCwwLTAuMDEtMC4wMi0wLjAxLTAuMDJjMC4wMSwwLjA5LDAuMDgsMC4wOSwwLjE1LDAuMQ0KCWMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xMmMwLDAtMC4wMSwwLTAuMDEsMA0KCWMwLjA4LDAuMDQsMC4xNiwwLjA4LDAuMjUsMC4xMmMwLDAtMC4wMSwwLTAuMDEsMGMwLjY2LDAuMzcsMS4zMiwwLjcyLDIuMDMsMC45OGMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjYsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA1LDAuMSwwLjE2LDAuMTEsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDgsMC4wNCwwLjE3LDAuMDksMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDgsMC4wNCwwLjE3LDAuMDksMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDksMC4xMiwwLjIyLDAuMTQsMC4zNSwwLjE2YzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDUsMC4xLDAuMTUsMC4xMSwwLjI0LDAuMTNjMCwwLDAtMC4wMSwwLTAuMDENCgljMC4wNSwwLjEsMC4xNSwwLjExLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAyLTAuMDEtMC4wMmMwLjA1LDAuMDMsMC4xLDAuMDcsMC4xNSwwLjFjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4wOCwwLjA0LDAuMTYsMC4wOCwwLjI1LDAuMTJjMCwwLTAuMDEsMC0wLjAxLDBjMC4xLDAuMSwwLjIyLDAuMTQsMC4zNSwwLjE2YzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuNDUsMC4zLDAuOTQsMC41MywxLjQ1LDAuNzJjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNywwLjEyLDAuMTksMC4xMiwwLjMxLDAuMTRjMC4wNiwwLjEzLDAuMTksMC4xNiwwLjMxLDAuMTkNCgljMC4wMywwLjAyLDAuMDcsMC4wMywwLjExLDAuMDNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNiwwLjEsMC4xNSwwLjExLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA5LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjE2LDAuMDgsMC4zMywwLjE2LDAuNDksMC4yNGMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xMmMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjY1LDAuMzksMS4zMSwwLjc0LDIuMDMsMC45OGMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjYsMC4xM2MwLDAtMC4wMi0wLjAxLTAuMDItMC4wMWMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA1LDAuMSwwLjE1LDAuMTIsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDUsMC4xLDAuMTYsMC4xMSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4wOCwwLjA0LDAuMTcsMC4wOCwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wOCwwLjA0LDAuMTcsMC4wOSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4wOCwwLjA0LDAuMTcsMC4wOCwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wOCwwLjA0LDAuMTcsMC4wOCwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC42OCwwLjM3LDEuMzYsMC43NCwyLjA5LDAuOTljMC4xNCwwLjE5LDAuMzUsMC4yNCwwLjU1LDAuMzFjMC4wMywwLjAyLDAuMDcsMC4wMywwLjEsMC4wM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA1LDAuMSwwLjE2LDAuMTEsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDgsMC4wNCwwLjE3LDAuMDksMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDYsMC4xLDAuMTYsMC4xMSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNiwwLjA5LDAuMTUsMC4xMiwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4xNiwwLjA4LDAuMzIsMC4xNiwwLjQ5LDAuMjVjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wOCwwLjA0LDAuMTcsMC4wOCwwLjI1LDAuMTJjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4yNSwwLjE4LDAuNTMsMC4zMSwwLjgyLDAuNGMwLDAsMC0wLjAyLDAtMC4wMmMwLjIxLDAuMTksMC40NiwwLjI5LDAuNzMsMC4zN2MwLDAtMC4wMS0wLjAyLTAuMDEtMC4wMg0KCWMwLjA1LDAuMTEsMC4xNSwwLjEyLDAuMjUsMC4xNGMwLDAtMC4wMS0wLjAyLTAuMDEtMC4wMmMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjYsMC4xM2MwLDAtMC4wMi0wLjAyLTAuMDItMC4wMg0KCWMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjUsMC4xNWMwLjEyLDAuMDYsMC4yNSwwLjEzLDAuMzcsMC4xOWMwLjAzLDAuMDIsMC4wNywwLjAzLDAuMTEsMC4wM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA5LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA2LDAuMSwwLjE1LDAuMTIsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDgsMC4wNCwwLjE3LDAuMDksMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDksMC4xMiwwLjIyLDAuMTQsMC4zNSwwLjE2YzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMSwwLjEzLDAuMjMsMC4xOCwwLjM4LDAuMjFjMCwwLTAuMDEsMC0wLjAxLDBjMC43NCwwLjQxLDEuNDksMC43OSwyLjI3LDEuMWMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA1LDAuMTEsMC4xNSwwLjEyLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA2LDAuMSwwLjE2LDAuMTEsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDYsMC4xLDAuMTUsMC4xMiwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNiwwLjEsMC4xNSwwLjEyLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA5LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjY2LDAuMzcsMS4zMywwLjcsMi4wMywwLjk4YzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDUsMC4xMSwwLjE2LDAuMTEsMC4yNiwwLjEzYzAsMC0wLjAyLTAuMDEtMC4wMi0wLjAxDQoJYzAuMDUsMC4xMSwwLjE1LDAuMTIsMC4yNSwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAuMDUsMC4xMSwwLjE2LDAuMTEsMC4yNiwwLjEzYzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxDQoJYzAuMDUsMC4xLDAuMTUsMC4xMSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNiwwLjEsMC4xNSwwLjExLDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjU0LDAuMzIsMS4xLDAuNTksMS42OCwwLjgyYzAuMjcsMC4xMywwLjU0LDAuMjUsMC44MSwwLjM4YzAuMDQsMC4xNCwwLjE2LDAuMTIsMC4yNiwwLjE0YzAsMC0wLjAyLTAuMDEtMC4wMi0wLjAxDQoJYzAuMDUsMC4xLDAuMTYsMC4xMSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDFjMC4wNSwwLjExLDAuMTYsMC4xMSwwLjI1LDAuMTNjMCwwLTAuMDEtMC4wMS0wLjAxLTAuMDENCgljMC4wOSwwLjEyLDAuMjEsMC4xNCwwLjM1LDAuMTZjMCwwLTAuMDEtMC4wMi0wLjAxLTAuMDJjMC4wMSwwLjEsMC4wOCwwLjA5LDAuMTUsMC4xMWMwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMQ0KCWMwLjA4LDAuMDQsMC4xNywwLjA4LDAuMjUsMC4xM2MwLDAtMC4wMS0wLjAxLTAuMDEtMC4wMWMwLjM4LDAuMjQsMC43OCwwLjQ0LDEuMjEsMC41OGwwLjAyLDAuMDNjMCwwLDAuMDQtMC4wMSwwLjA0LTAuMDENCgljMC4yMywwLjE5LDAuNDksMC4zLDAuNzcsMC4zOGMwLDAtMC4wMS0wLjAyLTAuMDEtMC4wMmMwLjA1LDAuMTEsMC4xNiwwLjEyLDAuMjYsMC4xM2MwLDAtMC4wMi0wLjAyLTAuMDItMC4wMg0KCWMwLjA1LDAuMTEsMC4xNiwwLjExLDAuMjYsMC4xM2MwLDAtMC4wMS0wLjAyLTAuMDEtMC4wMmMwLjA3LDAuMTMsMC4xOSwwLjE1LDAuMzIsMC4xMmMwLjYzLDAuNTIsMS40MSwwLjc1LDIuMTIsMS4xMQ0KCWMwLjU2LDAuMjksMS4wNiwwLjYyLDEuNDEsMS4xNWMwLjAxLDAuMDksMCwwLjIsMC4xMSwwLjI1YzAsMC0wLjAxLTAuMDEtMC4wMS0wLjAxYzAsMC43NCwwLDEuNDgsMC4wMSwyLjIyDQoJYy0wLjM0LDAuNDItMC42NCwwLjg4LTEuMiwxLjA2Yy0wLjExLDAuMDMtMC4yMywwLjA1LTAuMzQsMC4wOGMtMC4wOCwwLTAuMTYsMC0wLjI0LDBjLTAuNjMsMC0xLjI2LDAuMDItMS44OS0wLjAyDQoJYy0wLjQ0LTAuMDMtMC41LDAuMTQtMC41LDAuNTNjLTAuMDEsMC44NiwwLjE0LDEuNzMtMC4xMiwyLjU4YzAsMCwwLTAuMDEsMC0wLjAxQzkyLjYyLDQxLjg5LDkyLjU4LDQxLjk3LDkyLjUzLDQyLjA1eg0KCSBNNDcuNDEsNzUuNDljMC4xMiwwLDAuMjQsMCwwLjM2LDBjMS4yMywwLDIuNDcsMCwzLjcsMGMwLjU5LDAuMDksMC4zNC0wLjM3LDAuMzQtMC41OGMwLjAxLTExLjQ5LDAuMDEtMjIuOTksMC4wMS0zNC40OA0KCWMwLTAuMTYsMC4wMS0wLjMyLTAuMDEtMC40OGMtMC4wMi0wLjEyLTAuMTItMC4xMy0wLjIzLTAuMWMtMi45NCwwLTUuODgsMC4wMS04LjgyLDAuMDFjLTAuNTYtMC4xMi0wLjM0LDAuMzItMC4zNCwwLjUyDQoJYy0wLjAxLDExLjUsMCwyMy4wMS0wLjAzLDM0LjUxYzAsMC42MSwwLjIxLDAuNzIsMC43MiwwLjZjMC43MiwwLDEuNDMsMCwyLjE1LDBjMC4yNCwwLDAuNDgsMCwwLjcyLDBjMC4yLDAsMC40LDAsMC41OSwwDQoJYzAuMTIsMCwwLjI0LDAsMC4zNiwwQzQ3LjA5LDc1LjQ5LDQ3LjI1LDc1LjQ5LDQ3LjQxLDc1LjQ5eiBNNjMuMyw3NS40OGMwLjQsMCwwLjc5LDAsMS4xOSwwYzAuNjcsMCwxLjM0LDAsMi4wMSwwDQoJYzAuNDgsMCwwLjk3LDAsMS40NSwwYzAuMTIsMCwwLjI0LDAsMC4zNywwYzAuMjUtMC4xNSwwLjExLTAuMzksMC4xMS0wLjU4YzAuMDEtOC40NiwwLjAxLTE2LjkyLDAuMDEtMjUuMzcNCgljMC0zLjItMC4wMS02LjQxLTAuMDItOS42MWMtMC4xMi0wLjIxLTAuMzItMC4xLTAuNDctMC4xYy0xLjQyLTAuMDEtMi44NS0wLjAxLTQuMjctMC4wMWMtMS41MiwwLjAxLTMuMDQsMC4wMy00LjU2LDAuMDQNCgljLTAuMzMsMC4xNC0wLjE1LDAuNDItMC4xNSwwLjYzYy0wLjAxLDkuODMtMC4wMSwxOS42Ni0wLjAxLDI5LjQ5YzAsMS42OSwwLDMuMzgsMC4wMSw1LjA3YzAsMC4xNy0wLjE3LDAuNDksMC4yNSwwLjQ0DQoJYzAuMjEsMCwwLjQxLDAuMDEsMC42MiwwLjAxYzAuOTksMCwxLjk5LDAsMi45OCwwQzYyLjk4LDc1LjQ4LDYzLjE0LDc1LjQ4LDYzLjMsNzUuNDh6IE04NC42Nyw0MC4wM2MwLjAyLTAuMDQsMC4wMS0wLjA3LTAuMDEtMC4xMQ0KCWMtMC4wMi0wLjAzLTAuMDQtMC4wOS0wLjA2LTAuMDljLTIuOTktMC4wMS01Ljk5LTAuMDEtOC45OC0wLjAyYy0wLjE2LDAtMC4yOCwwLjAxLTAuMjUsMC4yMmMwLDcuMjMtMC4wMSwxNC40NS0wLjAxLDIxLjY4DQoJYzAsNC40MiwwLDguODQsMCwxMy4yNmMwLDAuMjUtMC4xMywwLjU1LDAuMzYsMC41NWMyLjk1LTAuMDMsNS44OS0wLjAyLDguODQtMC4wM2MwLjI1LTAuMTUsMC4xMS0wLjM5LDAuMTEtMC41OA0KCWMwLjAxLTMuMDgsMC4wMS02LjE3LDAuMDEtOS4yNWMwLTguNS0wLjAxLTE2Ljk5LTAuMDEtMjUuNDlDODQuNjcsNDAuMTEsODQuNjcsNDAuMDcsODQuNjcsNDAuMDN6IE0zNS40MywzOS45MQ0KCWMtMC4wNS0wLjAzLTAuMDktMC4wOC0wLjE0LTAuMDhjLTIuOTUsMC01LjksMC04Ljg1LDBjLTAuMTQsMC0wLjI5LTAuMDEtMC4yNiwwLjJjLTAuMDEsMC4wOC0wLjAyLDAuMTYtMC4wMiwwLjI0DQoJYzAsMTEuNTgsMCwyMy4xNi0wLjAxLDM0Ljc0YzAsMC40NSwwLjE2LDAuNSwwLjU1LDAuNWMyLjcxLTAuMDIsNS40MS0wLjAzLDguMTIsMGMwLjU2LDAuMDEsMC42Ny0wLjE1LDAuNjctMC42OA0KCWMtMC4wMi0xMS40Ni0wLjAxLTIyLjkyLTAuMDEtMzQuMzlDMzUuNDYsNDAuMjcsMzUuNDQsNDAuMDksMzUuNDMsMzkuOTF6Ii8+DQo8L3N2Zz4NCg==" type="image/svg+xml">

    <!-- D3.js -->
    <script src="https://d3js.org/d3.v7.min.js"></script>

    <!-- diff2html -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/diff2html/bundles/css/diff2html.min.css">
    <script src="https://cdn.jsdelivr.net/npm/diff2html/bundles/js/diff2html-ui.min.js"></script>

    <!-- highlight.js for syntax highlighting in diffs -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/python.min.js"></script>
    <!-- marked.js for markdown rendering -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>

    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }}

        header {{
            text-align: center;
            padding: 40px 20px;
            border-bottom: 1px solid #30363d;
            margin-bottom: 30px;
        }}

        header h1 {{
            color: #58a6ff;
            font-size: 2.5em;
            margin-bottom: 10px;
        }}

        header p {{
            color: #8b949e;
            font-size: 1.1em;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}

        .stat-card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }}

        .stat-card .value {{
            font-size: 2.5em;
            font-weight: bold;
            color: #58a6ff;
        }}

        .stat-card .label {{
            color: #8b949e;
            font-size: 0.9em;
            margin-top: 5px;
        }}

        .stat-card.success .value {{
            color: #3fb950;
        }}

        section {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            margin-bottom: 30px;
            overflow: hidden;
        }}

        section h2 {{
            padding: 15px 20px;
            background: #21262d;
            border-bottom: 1px solid #30363d;
            font-size: 1.2em;
            color: #c9d1d9;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .section-content {{
            padding: 20px;
        }}

        #chart-container {{
            min-height: 300px;
        }}

        #tree-container {{
            height: 600px;
            overflow: auto;
        }}

        #tree-svg {{
            width: 100%;
            min-height: 500px;
        }}

        .node circle {{
            stroke-width: 2px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .node circle:hover {{
            stroke-width: 4px;
        }}

        .node.best circle {{
            stroke: #ffd700 !important;
            stroke-width: 3px;
        }}

        .node text {{
            font-size: 11px;
            fill: #8b949e;
        }}

        .link {{
            fill: none;
            stroke: #30363d;
            stroke-width: 1.5px;
        }}

        /* Path highlighting for selected node ancestry */
        .node.on-path circle {{
            stroke: #58a6ff !important;
            stroke-width: 3px;
        }}

        .link.on-path {{
            stroke: #58a6ff !important;
            stroke-width: 3px;
        }}

        .tooltip {{
            position: absolute;
            background: #21262d;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 10px;
            pointer-events: none;
            z-index: 2000;
            max-width: 300px;
        }}

        .tooltip h4 {{
            color: #58a6ff;
            margin-bottom: 8px;
        }}

        .tooltip p {{
            margin: 4px 0;
            font-size: 0.9em;
        }}

        #detail-panel {{
            display: none;
        }}

        #detail-panel.active {{
            display: block;
        }}

        .detail-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}

        .detail-header h3 {{
            color: #58a6ff;
        }}

        .ancestry-path {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 4px;
            margin-bottom: 15px;
            font-size: 0.9em;
            color: #8b949e;
        }}

        .ancestry-path .path-node {{
            padding: 4px 8px;
            background: #21262d;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.2s;
        }}

        .ancestry-path .path-node:hover {{
            background: #30363d;
        }}

        .ancestry-path .path-node.current {{
            background: #388bfd;
            color: white;
        }}

        .ancestry-path .path-separator {{
            color: #484f58;
        }}

        .ancestry-path .path-node.future {{
            background: #161b22;
            color: #484f58;
            border: 1px dashed #30363d;
        }}

        .ancestry-path .path-node.future:hover {{
            background: #21262d;
            color: #8b949e;
        }}

        .ancestry-path .path-separator.future {{
            color: #30363d;
        }}

        .path-analysis {{
            margin: 15px 0;
            background: #161b22;
            border-radius: 6px;
        }}

        .path-analysis-header {{
            padding: 10px 15px;
            background: #21262d;
            color: #8b949e;
            font-size: 0.9em;
            font-weight: 500;
        }}

        #path-chart-container {{
            padding: 10px 15px 15px 15px;
        }}

        .close-btn {{
            background: #21262d;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
        }}

        .close-btn:hover {{
            background: #30363d;
        }}

        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}

        .metric-item {{
            background: #21262d;
            padding: 12px;
            border-radius: 6px;
        }}

        .metric-item .metric-label {{
            font-size: 0.8em;
            color: #8b949e;
        }}

        .metric-item .metric-value {{
            font-size: 1.4em;
            font-weight: bold;
            color: #c9d1d9;
        }}

        .diff-container {{
            margin-top: 20px;
            border-radius: 6px;
            overflow: hidden;
        }}

        /* Fix diff2html overflow */
        #tab-diff {{
            overflow: auto;
        }}

        .diff-container .d2h-wrapper {{
            overflow: visible;
        }}

        .diff-container .d2h-file-wrapper {{
            border: none;
            margin-bottom: 0;
        }}

        .diff-container .d2h-file-diff {{
            overflow: visible;
        }}

        .diff-container .d2h-diff-table {{
            width: 100%;
        }}

        /* Ensure line numbers don't have sticky positioning */
        .diff-container .d2h-code-linenumber,
        .diff-container .d2h-code-side-linenumber {{
            position: static !important;
        }}

        .diff-container h4 {{
            background: #21262d;
            padding: 10px 15px;
            margin: 0;
        }}

        /* Code file sections */
        .file-section {{
            margin-bottom: 12px;
            border: 1px solid #30363d;
            border-radius: 6px;
            overflow: hidden;
        }}

        .file-section:first-child {{
            margin-top: 15px;
        }}

        .file-header {{
            padding: 8px 12px;
            background: #161b22;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            user-select: none;
        }}

        .file-header:hover {{
            background: #21262d;
        }}

        .collapse-icon {{
            color: #8b949e;
            transition: transform 0.2s;
            font-size: 0.8em;
        }}

        .file-section.collapsed .collapse-icon {{
            transform: rotate(-90deg);
        }}

        .file-section.collapsed .file-content {{
            display: none;
        }}

        .file-path {{
            font-family: monospace;
            color: #58a6ff;
            font-size: 0.9em;
        }}

        .file-stats {{
            margin-left: auto;
            color: #8b949e;
            font-size: 0.85em;
        }}

        .file-actions {{
            display: flex;
            gap: 8px;
        }}

        .file-actions button {{
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 2px 8px;
            cursor: pointer;
            font-size: 11px;
        }}

        .file-actions button:hover {{
            background: #30363d;
        }}

        .file-content {{
            display: flex;
            background: #0d1117;
        }}

        .file-content .line-numbers {{
            padding: 12px 10px 12px 12px;
            text-align: right;
            color: #6e7681;
            font-family: monospace;
            font-size: 13px;
            line-height: 1.45;
            user-select: none;
            border-right: 1px solid #30363d;
            background: #0d1117;
            flex-shrink: 0;
        }}

        .file-content pre {{
            margin: 0;
            padding: 12px;
            background: #0d1117;
            overflow-x: auto;
            flex: 1;
        }}

        .file-content code {{
            font-size: 13px;
            line-height: 1.45;
        }}

        .feedback-section {{
            margin-top: 20px;
            padding: 15px;
            background: #21262d;
            border-radius: 6px;
        }}

        .feedback-section h4 {{
            color: #8b949e;
            margin-bottom: 10px;
        }}

        .feedback-section p {{
            white-space: pre-wrap;
        }}

        .analysis-section {{
            margin-top: 20px;
            padding: 15px;
            background: #21262d;
            border-radius: 6px;
        }}

        .analysis-content {{
            font-size: 0.95em;
            line-height: 1.7;
            color: #c9d1d9;
        }}

        .analysis-content h1, .analysis-content h2, .analysis-content h3,
        .analysis-content h4, .analysis-content h5, .analysis-content h6 {{
            color: #58a6ff;
            margin-top: 1.2em;
            margin-bottom: 0.6em;
            margin-left: 0;
            padding-left: 0;
            font-weight: 600;
        }}

        .analysis-content h1 {{ font-size: 1.5em; border-bottom: 1px solid #30363d; padding-bottom: 0.3em; }}
        .analysis-content h2 {{ font-size: 1.3em; border-bottom: 1px solid #30363d; padding-bottom: 0.3em; }}
        .analysis-content h3 {{ font-size: 1.1em; }}

        .analysis-content p {{
            margin-bottom: 1em;
        }}

        .analysis-content ul, .analysis-content ol {{
            padding-left: 2em;
            margin-bottom: 1em;
        }}

        .analysis-content li {{
            margin-bottom: 0.4em;
        }}

        .analysis-content code {{
            background: #161b22;
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-family: monospace;
            font-size: 0.9em;
        }}

        .analysis-content pre {{
            background: #161b22;
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 1em;
        }}

        .analysis-content pre code {{
            background: none;
            padding: 0;
        }}

        .analysis-content strong {{
            color: #f0f6fc;
        }}

        .analysis-content blockquote {{
            border-left: 3px solid #3fb950;
            padding-left: 1em;
            color: #8b949e;
            margin: 1em 0;
        }}

        #heatmap-container {{
            height: 400px;
        }}

        .heatmap-cell {{
            cursor: pointer;
            transition: opacity 0.2s;
        }}

        .heatmap-cell:hover {{
            opacity: 0.8;
        }}

        .legend {{
            display: flex;
            align-items: center;
            justify-content: center;
            margin-top: 15px;
            gap: 20px;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.9em;
            color: #8b949e;
        }}

        .legend-color {{
            width: 20px;
            height: 20px;
            border-radius: 4px;
        }}

        /* diff2html overrides */
        .d2h-wrapper {{
            background: #0d1117 !important;
        }}

        .d2h-file-header {{
            background: #21262d !important;
            border-color: #30363d !important;
        }}

        .d2h-file-name {{
            color: #c9d1d9 !important;
        }}

        .d2h-code-line {{
            background: #0d1117 !important;
        }}

        .d2h-code-line-ctn {{
            background: #0d1117 !important;
            color: #c9d1d9 !important;
        }}

        .d2h-del {{
            background-color: rgba(248, 81, 73, 0.25) !important;
        }}

        .d2h-del .d2h-code-line-ctn {{
            color: #ffa198 !important;
        }}

        .d2h-ins {{
            background-color: rgba(63, 185, 80, 0.25) !important;
        }}

        .d2h-ins .d2h-code-line-ctn {{
            color: #7ee787 !important;
        }}

        /* Inline deletions and insertions */
        .d2h-code-line-ctn del {{
            background-color: rgba(248, 81, 73, 0.4) !important;
            text-decoration: none !important;
            border-radius: 2px;
            padding: 0 2px;
        }}

        .d2h-code-line-ctn ins {{
            background-color: rgba(63, 185, 80, 0.4) !important;
            text-decoration: none !important;
            border-radius: 2px;
            padding: 0 2px;
        }}

        .d2h-code-line-prefix {{
            color: #8b949e !important;
        }}

        /* diff2html line number overrides */
        .d2h-code-linenumber {{
            background: #161b22 !important;
            color: #8b949e !important;
            border-color: #30363d !important;
        }}

        .d2h-code-side-linenumber {{
            background: #161b22 !important;
            color: #8b949e !important;
            border-color: #30363d !important;
        }}

        .d2h-file-wrapper {{
            border-color: #30363d !important;
        }}

        .d2h-file-diff {{
            border-color: #30363d !important;
        }}

        .d2h-diff-table {{
            border-color: #30363d !important;
        }}

        .d2h-emptyplaceholder {{
            background: #161b22 !important;
            border-color: #30363d !important;
        }}

        .d2h-file-side-diff {{
            background: #0d1117 !important;
        }}

        .d2h-diff-tbody tr {{
            background: #0d1117 !important;
        }}

        .d2h-info {{
            background: #161b22 !important;
            color: #8b949e !important;
            border-color: #30363d !important;
        }}

        /* diff2html context and empty placeholder overrides */
        .d2h-cntx {{
            background: #0d1117 !important;
        }}

        .d2h-code-side-emptyplaceholder {{
            background: #161b22 !important;
            border-color: #30363d !important;
        }}

        /* Side-by-side diff empty areas */
        .d2h-file-side-diff .d2h-emptyplaceholder,
        .d2h-file-side-diff .d2h-code-side-emptyplaceholder {{
            background: #161b22 !important;
        }}

        /* Ensure all table cells have dark background */
        .d2h-diff-table td {{
            background: #0d1117 !important;
            border-color: #30363d !important;
        }}

        .d2h-diff-table td.d2h-code-side-linenumber,
        .d2h-diff-table td.d2h-code-linenumber {{
            background: #161b22 !important;
        }}

        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #8b949e;
        }}

        .metric-selector {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding: 15px;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
        }}

        .metric-selector label {{
            color: #8b949e;
            font-size: 0.9em;
        }}

        .metric-selector select {{
            background: #21262d;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.95em;
            cursor: pointer;
            min-width: 180px;
        }}

        .metric-selector select:focus {{
            outline: none;
            border-color: #58a6ff;
        }}

        .metric-selector select option {{
            background: #21262d;
            color: #c9d1d9;
        }}

        footer {{
            text-align: center;
            padding: 30px 20px;
            margin-top: 40px;
            border-top: 1px solid #30363d;
            color: #8b949e;
            font-size: 0.9em;
        }}

        footer a {{
            color: #58a6ff;
            text-decoration: none;
        }}

        footer a:hover {{
            text-decoration: underline;
        }}

        .config-table {{
            width: 100%;
            border-collapse: collapse;
        }}

        .config-table tr {{
            border-bottom: 1px solid #30363d;
        }}

        .config-table tr:last-child {{
            border-bottom: none;
        }}

        .config-table td {{
            padding: 10px 15px;
            vertical-align: top;
        }}

        .config-table .config-key {{
            width: 280px;
            color: #8b949e;
            font-size: 0.9em;
        }}

        .config-table .config-value {{
            color: #c9d1d9;
            font-family: monospace;
            font-size: 0.9em;
            word-break: break-word;
        }}

        .tabs {{
            display: flex;
            border-bottom: 1px solid #30363d;
            background: #21262d;
        }}

        .tab {{
            padding: 12px 20px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            color: #8b949e;
            transition: all 0.2s;
        }}

        .tab:hover {{
            color: #c9d1d9;
        }}

        .tab.active {{
            color: #58a6ff;
            border-bottom-color: #58a6ff;
        }}

        .tab-content {{
            display: none;
            height: 1000px;
            overflow-y: auto;
        }}

        .tab-content.active {{
            display: block;
        }}

        .prompt-subtabs {{
            display: flex;
            gap: 8px;
            padding: 10px 15px;
            background: #161b22;
            border-bottom: 1px solid #30363d;
        }}

        .prompt-subtab {{
            padding: 6px 12px;
            background: #21262d;
            border: 1px solid #30363d;
            border-radius: 4px;
            color: #8b949e;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
        }}

        .prompt-subtab:hover {{
            background: #30363d;
            color: #c9d1d9;
        }}

        .prompt-subtab.active {{
            background: #388bfd;
            border-color: #388bfd;
            color: white;
        }}

        .expand-btn {{
            background: #21262d;
            border: 1px solid #30363d;
            color: #8b949e;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 1.2em;
        }}

        .expand-btn:hover {{
            background: #30363d;
            color: #c9d1d9;
        }}

        /* Tree Explorer Modal */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            z-index: 1000;
        }}

        .modal.active {{
            display: flex;
            justify-content: center;
            align-items: center;
        }}

        .modal-content.tree-explorer {{
            width: 100vw;
            height: 100vh;
            background: #0d1117;
            border-radius: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}

        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            background: #161b22;
            border-bottom: 1px solid #30363d;
        }}

        .modal-header h2 {{
            margin: 0;
            color: #c9d1d9;
            font-size: 1.3em;
        }}

        .modal-close-btn {{
            background: none;
            border: none;
            color: #8b949e;
            font-size: 28px;
            cursor: pointer;
            padding: 0 8px;
            line-height: 1;
        }}

        .modal-close-btn:hover {{
            color: #f85149;
        }}

        .modal-body {{
            display: flex;
            flex: 1;
            overflow: hidden;
        }}

        .explorer-left {{
            width: 60%;
            min-width: 300px;
            overflow: auto;
            padding: 15px;
            background: #0d1117;
        }}

        .explorer-resizer {{
            width: 6px;
            background: #30363d;
            cursor: col-resize;
            transition: background 0.2s;
            flex-shrink: 0;
        }}

        .explorer-resizer:hover,
        .explorer-resizer.dragging {{
            background: #58a6ff;
        }}

        .explorer-right {{
            width: 40%;
            min-width: 300px;
            overflow: auto;
            background: #161b22;
        }}

        #explorer-tree-container {{
            min-height: 100%;
        }}

        #explorer-detail-panel {{
            height: 100%;
        }}

        #explorer-detail-panel .explorer-detail-title {{
            padding: 15px 20px;
            background: #21262d;
            border-bottom: 1px solid #30363d;
            font-size: 1.1em;
            font-weight: 600;
            color: #c9d1d9;
        }}

        #explorer-detail-panel .ancestry-path {{
            padding: 10px 20px;
            background: #161b22;
            border-bottom: 1px solid #30363d;
            margin-bottom: 0;
        }}

        #explorer-detail-panel .explorer-metrics {{
            padding: 15px 20px;
            border-bottom: 1px solid #30363d;
        }}

        #explorer-detail-panel .explorer-metrics-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
        }}

        #explorer-detail-panel .explorer-metric-item {{
            background: #21262d;
            padding: 10px;
            border-radius: 4px;
        }}

        #explorer-detail-panel .explorer-metric-label {{
            font-size: 0.75em;
            color: #8b949e;
            margin-bottom: 2px;
        }}

        #explorer-detail-panel .explorer-metric-value {{
            font-size: 1.1em;
            font-weight: 600;
            color: #c9d1d9;
        }}

        .explorer-empty-state {{
            color: #8b949e;
            text-align: center;
            padding: 40px 20px;
        }}

        .explorer-mutation-summary {{
            padding: 15px 20px;
            background: #21262d;
            border-bottom: 1px solid #30363d;
            color: #8b949e;
            font-size: 0.9em;
        }}

        .explorer-mutation-summary strong {{
            color: #c9d1d9;
        }}

        /* Explorer tab content padding */
        #explorer-detail-panel .tab-content {{
            padding: 15px 20px;
            height: auto;
            overflow-y: visible;
        }}

        /* Explorer tab content active state - override default */
        #explorer-detail-panel .tab-content.active {{
            display: block;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Evolution Report</h1>
            <p>Generated by Pantheon-Evolve</p>
        </header>

        <!-- Summary Stats -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="value" id="stat-iterations">-</div>
                <div class="label">Total Programs</div>
            </div>
            <div class="stat-card success">
                <div class="value" id="stat-best-score">-</div>
                <div class="label">Best Score</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-improvement">-</div>
                <div class="label">Improvement</div>
            </div>
            <div class="stat-card">
                <div class="value" id="stat-islands">-</div>
                <div class="label">Islands</div>
            </div>
        </div>

        <!-- Score History Chart -->
        <section>
            <h2>Score History</h2>
            <div class="section-content">
                <div style="margin-bottom: 15px;">
                    <label style="color: #8b949e; cursor: pointer;">
                        <input type="checkbox" id="hide-failed" checked style="margin-right: 6px;">
                        Hide failed evaluations
                    </label>
                </div>
                <div id="chart-container"></div>
            </div>
        </section>

        <!-- Metric Selector -->
        <div class="metric-selector">
            <label for="best-metric-select">Color by metric:</label>
            <select id="best-metric-select">
                <!-- Options will be populated by JavaScript -->
            </select>
            <div id="color-legend" style="display: inline-flex; align-items: center; margin-left: 15px;">
                <span id="legend-min" style="color: #8b949e; font-size: 0.8em; margin-right: 5px;">0.00</span>
                <div style="width: 120px; height: 12px; background: linear-gradient(to right, #f85149, #d29922, #3fb950); border-radius: 3px;"></div>
                <span id="legend-max" style="color: #8b949e; font-size: 0.8em; margin-left: 5px;">1.00</span>
            </div>
        </div>

        <!-- Evolution Tree -->
        <section>
            <h2>
                Evolution Tree
                <button class="expand-btn" onclick="openTreeExplorer()" title="Open fullscreen explorer">&#x26F6;</button>
            </h2>
            <div class="section-content">
                <p style="color: #8b949e; margin-bottom: 15px;">
                    Click on a node to view details. Green = high score, Red = low score.
                </p>
                <div id="tree-container">
                    <svg id="tree-svg"></svg>
                </div>
            </div>
        </section>

        <!-- Program Detail Panel -->
        <section id="detail-panel">
            <h2>Program Details</h2>
            <div class="section-content">
                <div class="detail-header">
                    <h3 id="detail-title">Program: -</h3>
                    <button class="close-btn" onclick="closeDetailPanel()">Close</button>
                </div>

                <div class="ancestry-path" id="ancestry-path"></div>

                <div class="path-analysis" id="path-analysis">
                    <div class="path-analysis-header">
                        <span>Path Analysis</span>
                    </div>
                    <div id="path-chart-container"></div>
                </div>

                <div class="metrics-grid" id="detail-metrics"></div>

                <div class="tabs">
                    <div class="tab active" data-tab="diff">Diff</div>
                    <div class="tab" data-tab="analysis">Analysis</div>
                    <div class="tab" data-tab="prompt">Prompt</div>
                    <div class="tab" data-tab="feedback">LLM Feedback</div>
                    <div class="tab" data-tab="code">Code Preview</div>
                </div>

                <div class="tab-content active" id="tab-diff">
                    <div class="diff-container" id="diff-view"></div>
                </div>

                <div class="tab-content" id="tab-analysis">
                    <div class="analysis-section" id="analysis-view"></div>
                </div>

                <div class="tab-content" id="tab-prompt">
                    <div class="prompt-subtabs">
                        <button class="prompt-subtab active" data-prompt="analyzer">Analyzer Prompt</button>
                        <button class="prompt-subtab" data-prompt="mutator">Mutator Prompt</button>
                    </div>
                    <div class="prompt-section" id="prompt-view"></div>
                </div>

                <div class="tab-content" id="tab-feedback">
                    <div class="feedback-section" id="feedback-view"></div>
                </div>

                <div class="tab-content" id="tab-code">
                    <div id="code-files-container"></div>
                </div>
            </div>
        </section>

        <!-- MAP-Elites Heatmap -->
        <section>
            <h2>MAP-Elites Grid</h2>
            <div class="section-content">
                <div class="heatmap-controls" style="display: flex; gap: 20px; margin-bottom: 15px; flex-wrap: wrap; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <label for="island-select" style="color: #8b949e; font-size: 0.9em;">Island:</label>
                        <select id="island-select" style="background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 6px 10px; font-size: 0.9em;">
                            <option value="all">All Islands</option>
                        </select>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <label for="x-dim-select" style="color: #8b949e; font-size: 0.9em;">X-Axis:</label>
                        <select id="x-dim-select" style="background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 6px 10px; font-size: 0.9em;">
                        </select>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <label for="y-dim-select" style="color: #8b949e; font-size: 0.9em;">Y-Axis:</label>
                        <select id="y-dim-select" style="background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 6px 10px; font-size: 0.9em;">
                        </select>
                    </div>
                </div>
                <div id="heatmap-container"></div>
                <div class="legend" style="display: flex; gap: 20px; flex-wrap: wrap; align-items: center;">
                    <div class="legend-item">
                        <div class="legend-color" style="background: #f85149;"></div>
                        <span>Low Score</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #d29922;"></div>
                        <span>Medium</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #3fb950;"></div>
                        <span>High Score</span>
                    </div>
                    <div id="island-legend" style="display: flex; gap: 10px; margin-left: 20px;"></div>
                </div>
            </div>
        </section>
    </div>

    <!-- Tooltip -->
    <div class="tooltip" id="tooltip" style="display: none;"></div>

    <script>
        // Embedded data
        const treeData = {tree_json};
        const scoreHistory = {history_json};
        const programsData = {programs_json};
        const mapElitesData = {map_elites_json};
        const summaryStats = {stats_json};
        const metricKeys = {metric_keys_json};
        const objectiveText = {objective_json};

        // Color palette for metrics
        const metricColors = {{
            'fitness_score': '#58a6ff',
            'function_score': '#e3b341',
            'llm_score': '#a371f7',
            'mixing_score': '#f778ba',
            'bio_conservation_score': '#3fb950',
            'speed_score': '#f0883e',
            'convergence_score': '#ffa657',
            'execution_time': '#8b949e',
            'iterations': '#6e7681',
            'fidelity': '#3fb950',
            'coverage': '#f0883e',
        }};

        // Default color for unknown metrics
        const defaultColors = ['#58a6ff', '#a371f7', '#3fb950', '#f0883e', '#f778ba', '#79c0ff', '#ffa657', '#ff7b72'];

        function getMetricColor(metric, index) {{
            return metricColors[metric] || defaultColors[index % defaultColors.length];
        }}

        // Track which metrics are visible
        const visibleMetrics = new Set(['fitness_score', 'best_fitness_score']);

        // Currently selected metric for coloring
        let selectedMetric = 'fitness_score';

        // Store tree root for ancestry path lookup
        let treeRoot = null;

        // Store chart context for selection line
        let chartContext = null;

        // Color scale for scores (will be updated based on selected metric)
        // Use .clamp(true) to prevent extreme extrapolation for values outside domain
        const colorScale = d3.scaleLinear()
            .domain([0, 0.5, 1])
            .range(['#f85149', '#d29922', '#3fb950'])
            .clamp(true);

        // Compute color scale domain based on selected metric
        function updateColorScale() {{
            // Collect all NUMERIC values for the selected metric
            // Filter out zero values (typically from failed evaluations) for better contrast
            const values = [];
            for (const prog of Object.values(programsData)) {{
                if (prog.metrics && prog.metrics[selectedMetric] !== undefined) {{
                    const val = prog.metrics[selectedMetric];
                    // Only include valid positive numbers (filter out null/NaN/Inf/zero)
                    if (typeof val === 'number' && !isNaN(val) && isFinite(val) && val > 0.001) {{
                        values.push(val);
                    }}
                }}
            }}
            let minVal = 0, maxVal = 1;
            if (values.length > 0) {{
                minVal = Math.min(...values);
                maxVal = Math.max(...values);

                // Ensure minimum spread for visual differentiation
                const range = maxVal - minVal;
                if (range < 0.05) {{
                    // If range is too small, expand it for better contrast
                    const center = (minVal + maxVal) / 2;
                    minVal = center - 0.025;
                    maxVal = center + 0.025;
                }}

                const midVal = (minVal + maxVal) / 2;
                colorScale.domain([minVal, midVal, maxVal]);
            }} else {{
                // Use default domain if no valid values
                colorScale.domain([0, 0.5, 1]);
            }}

            // Update legend labels
            document.getElementById('legend-min').textContent = minVal.toFixed(3);
            document.getElementById('legend-max').textContent = maxVal.toFixed(3);
        }}

        // Get score for a program based on selected metric
        function getScore(data) {{
            if (data.metrics && data.metrics[selectedMetric] !== undefined) {{
                return data.metrics[selectedMetric];
            }}
            return data.score || 0;
        }}

        // Helper to safely get color from scale (handles edge cases)
        function safeColor(value) {{
            if (value === undefined || value === null || isNaN(value)) {{
                return '#d29922';  // Fallback to yellow for invalid values
            }}
            const color = colorScale(value);
            // D3 may return rgb(0,0,0) for edge cases
            if (!color || color === 'rgb(0, 0, 0)') {{
                return '#d29922';  // Fallback to yellow
            }}
            return color;
        }}

        // Find best program based on selected metric
        function findBestProgramId() {{
            let bestId = null;
            let bestScore = -Infinity;
            for (const [id, prog] of Object.entries(programsData)) {{
                const score = prog.metrics && prog.metrics[selectedMetric] !== undefined
                    ? prog.metrics[selectedMetric]
                    : 0;
                if (score > bestScore) {{
                    bestScore = score;
                    bestId = id;
                }}
            }}
            return bestId;
        }}

        // Initialize metric selector
        function initMetricSelector() {{
            const select = document.getElementById('best-metric-select');
            // Get unique metrics (exclude best_* variants)
            const metrics = metricKeys.filter(k => !k.startsWith('best_'));
            metrics.forEach(metric => {{
                const option = document.createElement('option');
                option.value = metric;
                option.textContent = metric.replace(/_/g, ' ');
                if (metric === 'fitness_score') {{
                    option.selected = true;
                }}
                select.appendChild(option);
            }});

            // Handle selection change
            select.addEventListener('change', (e) => {{
                selectedMetric = e.target.value;
                updateColorScale();
                // Re-render tree and heatmap
                d3.select('#tree-svg').selectAll('*').remove();
                d3.select('#heatmap-container').selectAll('*').remove();
                renderTree();
                renderHeatmap();
            }});
        }}

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {{
            initMetricSelector();
            updateColorScale();
            renderStats();
            renderObjective();
            renderScoreChart();
            renderTree();
            renderHeatmap();
            setupTabs();

            // Re-render chart when filter checkbox changes
            document.getElementById('hide-failed').addEventListener('change', () => {{
                renderScoreChart();
            }});
        }});

        // Render summary stats
        function renderStats() {{
            document.getElementById('stat-iterations').textContent = summaryStats.total_iterations;
            document.getElementById('stat-best-score').textContent = summaryStats.best_score.toFixed(4);
            document.getElementById('stat-improvement').textContent =
                (summaryStats.improvement_pct >= 0 ? '+' : '') + summaryStats.improvement_pct.toFixed(1) + '%';
            document.getElementById('stat-islands').textContent = summaryStats.num_islands;
        }}

        // Render optimization objective
        function renderObjective() {{
            const section = document.getElementById('objective-section');
            const content = document.getElementById('objective-content');
            if (!objectiveText || objectiveText.trim() === '') {{
                // Hide objective section, make config full width
                section.style.display = 'none';
                section.parentElement.style.gridTemplateColumns = '1fr';
                return;
            }}
            section.style.display = 'block';
            // Convert markdown-like syntax to simple HTML
            let html = objectiveText
                .replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/\\n- /g, '\\n• ')
                .replace(/\\n(\\d+)\\. /g, '\\n$1. ');
            content.innerHTML = html;
        }}

        // Get filtered score history based on checkbox state
        // Instead of filtering (which changes array length), we mark hidden entries
        function getFilteredScoreHistory() {{
            const hideFailed = document.getElementById('hide-failed').checked;
            if (!hideFailed) {{
                // Reset _hidden flag when showing all
                return scoreHistory.map(d => ({{ ...d, _hidden: false }}));
            }}
            // Mark failed evaluations as hidden instead of filtering them out
            return scoreHistory.map(d => {{
                if (d.function_score && d.function_score > 0) {{
                    return {{ ...d, _hidden: false }};
                }} else {{
                    return {{ ...d, _hidden: true }};
                }}
            }});
        }}

        // Render score history chart with multi-metric support
        function renderScoreChart() {{
            // Clear existing chart content
            d3.select('#chart-container').selectAll('*').remove();

            const container = document.getElementById('chart-container');
            const width = container.clientWidth;
            const height = 320;
            const margin = {{top: 20, right: 30, bottom: 80, left: 60}};

            // Get filtered data
            const chartData = getFilteredScoreHistory();

            const svg = d3.select('#chart-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            if (chartData.length === 0) {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No score history available');
                return;
            }}

            // Build list of all metrics for charting
            // Include *_score metrics plus important metrics like fidelity, coverage
            // Exclude: best_* variants, fitness_weights, complexity, n_correct, n_samples (non-normalized)
            const excludeMetrics = ['fitness_weights', 'complexity', 'n_correct', 'n_samples'];
            const allMetrics = metricKeys.filter(k =>
                !k.startsWith('best_') &&
                !excludeMetrics.includes(k) &&
                (k.endsWith('_score') || ['fidelity', 'coverage'].includes(k))
            );
            const bestMetrics = metricKeys.filter(k =>
                k.startsWith('best_') &&
                !excludeMetrics.some(e => k.includes(e)) &&
                (k.endsWith('_score') || k.includes('fidelity') || k.includes('coverage'))
            );

            // Function to compute y-domain based on visible metrics only (excluding hidden points)
            function computeYDomain() {{
                let minVal = Infinity, maxVal = -Infinity;
                chartData.forEach(d => {{
                    if (d._hidden) return;  // Skip hidden points for y-axis range
                    visibleMetrics.forEach(m => {{
                        if (d[m] !== undefined) {{
                            minVal = Math.min(minVal, d[m]);
                            maxVal = Math.max(maxVal, d[m]);
                        }}
                    }});
                }});
                if (minVal === Infinity) {{ minVal = 0; maxVal = 1; }}
                const range = maxVal - minVal || 1;
                const padding = range * 0.1;
                return [Math.max(0, minVal - padding), maxVal + padding];
            }}

            // Use order field for x-axis to maintain consistent iteration numbers
            const maxOrder = Math.max(...chartData.map(d => d.order !== undefined ? d.order : 0));
            const x = d3.scaleLinear()
                .domain([0, maxOrder])
                .range([margin.left, width - margin.right]);

            const y = d3.scaleLinear()
                .domain(computeYDomain())
                .range([height - margin.bottom, margin.top]);

            // Grid lines group (will be updated dynamically)
            const gridGroup = svg.append('g')
                .attr('class', 'grid-lines')
                .attr('stroke', '#30363d')
                .attr('stroke-opacity', 0.5);

            // Y-axis group (will be updated dynamically)
            const yAxisGroup = svg.append('g')
                .attr('class', 'y-axis')
                .attr('transform', `translate(${{margin.left}},0)`)
                .attr('color', '#8b949e');

            // Create a group for lines
            const linesGroup = svg.append('g').attr('class', 'lines-group');

            // Draw lines for each metric (and update y-axis dynamically)
            function drawLines() {{
                // Recalculate y-domain based on visible metrics
                y.domain(computeYDomain());

                // Update grid lines
                gridGroup.selectAll('*').remove();
                gridGroup.selectAll('line')
                    .data(y.ticks(5))
                    .join('line')
                    .attr('x1', margin.left)
                    .attr('x2', width - margin.right)
                    .attr('y1', d => y(d))
                    .attr('y2', d => y(d));

                // Update y-axis
                yAxisGroup.call(d3.axisLeft(y).ticks(5));

                // Clear and redraw lines
                linesGroup.selectAll('*').remove();

                allMetrics.forEach((metric, idx) => {{
                    const color = getMetricColor(metric, idx);

                    // Current value line - filter out hidden points for continuous line
                    if (visibleMetrics.has(metric)) {{
                        const visibleData = chartData.filter(d => !d._hidden && d[metric] !== undefined);
                        const line = d3.line()
                            .x(d => x(d.order !== undefined ? d.order : 0))
                            .y(d => y(d[metric] || 0));

                        linesGroup.append('path')
                            .datum(visibleData)
                            .attr('fill', 'none')
                            .attr('stroke', color)
                            .attr('stroke-width', 1.5)
                            .attr('stroke-opacity', 0.8)
                            .attr('d', line);
                    }}

                    // Best value line (dashed, thicker) - filter out hidden points
                    const bestKey = 'best_' + metric;
                    if (visibleMetrics.has(bestKey)) {{
                        const bestVisibleData = chartData.filter(d => !d._hidden && d[bestKey] !== undefined);
                        const bestLine = d3.line()
                            .x(d => x(d.order !== undefined ? d.order : 0))
                            .y(d => y(d[bestKey] || 0));

                        linesGroup.append('path')
                            .datum(bestVisibleData)
                            .attr('fill', 'none')
                            .attr('stroke', color)
                            .attr('stroke-width', 2.5)
                            .attr('stroke-dasharray', '5,3')
                            .attr('d', bestLine);
                    }}
                }});
            }}

            drawLines();

            // X-axis (static)
            svg.append('g')
                .attr('transform', `translate(0,${{height - margin.bottom}})`)
                .call(d3.axisBottom(x).ticks(10))
                .attr('color', '#8b949e');

            // Axis labels
            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height - margin.bottom + 35)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text('Iteration');

            svg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2 + margin.bottom / 2)
                .attr('y', 15)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text('Score');

            // Hover interaction elements
            const focus = svg.append('g').style('display', 'none');

            // Vertical indicator line
            focus.append('line')
                .attr('class', 'hover-line')
                .attr('y1', margin.top)
                .attr('y2', height - margin.bottom)
                .attr('stroke', '#58a6ff')
                .attr('stroke-width', 1)
                .attr('stroke-dasharray', '4,4');

            // Selection indicator line (shown when a program is selected)
            const selectionLine = svg.append('line')
                .attr('class', 'selection-line')
                .attr('y1', margin.top)
                .attr('y2', height - margin.bottom)
                .attr('stroke', '#f0883e')
                .attr('stroke-width', 2)
                .style('display', 'none');

            // Store chart context for use in showProgramDetail
            chartContext = {{ svg, x, margin, height, selectionLine, chartData }};

            // Transparent overlay to capture mouse events
            svg.append('rect')
                .attr('class', 'chart-overlay')
                .attr('x', margin.left)
                .attr('y', margin.top)
                .attr('width', width - margin.left - margin.right)
                .attr('height', height - margin.top - margin.bottom)
                .style('fill', 'none')
                .style('pointer-events', 'all')
                .style('cursor', 'pointer')
                .on('mouseover', () => focus.style('display', null))
                .on('mouseout', () => {{
                    focus.style('display', 'none');
                    hideTooltip();
                }})
                .on('mousemove', function(event) {{
                    // Find the nearest data point by order value
                    const mouseX = d3.pointer(event)[0];
                    const targetOrder = Math.round(x.invert(mouseX));

                    // Find the data point with the closest order value (only non-hidden)
                    let nearestPoint = null;
                    let minDist = Infinity;
                    chartData.forEach(d => {{
                        if (d._hidden) return;
                        const order = d.order !== undefined ? d.order : 0;
                        const dist = Math.abs(order - targetOrder);
                        if (dist < minDist) {{
                            minDist = dist;
                            nearestPoint = d;
                        }}
                    }});

                    if (!nearestPoint) return;

                    const dataPoint = nearestPoint;
                    const pointOrder = dataPoint.order !== undefined ? dataPoint.order : 0;

                    // Update vertical line position using order
                    focus.select('.hover-line').attr('x1', x(pointOrder)).attr('x2', x(pointOrder));

                    // Build tooltip content showing all visible metrics
                    const tooltip = document.getElementById('tooltip');
                    let html = `<h4>Iteration ${{pointOrder}}</h4>`;
                    html += `<p style="color: #8b949e; font-size: 0.85em;">Program: ${{dataPoint.program_id ? dataPoint.program_id.substring(0, 8) : '-'}}</p>`;
                    html += '<hr style="border-color: #30363d; margin: 8px 0;">';

                    // Show values for all visible metrics
                    let metricIndex = 0;
                    allMetrics.forEach((metric, idx) => {{
                        const color = getMetricColor(metric, idx);
                        if (visibleMetrics.has(metric) && dataPoint[metric] !== undefined) {{
                            const value = dataPoint[metric].toFixed(4);
                            html += `<p><span style="display:inline-block;width:10px;height:10px;background:${{color}};border-radius:2px;margin-right:6px;"></span><strong>${{metric.replace(/_/g, ' ')}}:</strong> ${{value}}</p>`;
                        }}
                        const bestKey = 'best_' + metric;
                        if (visibleMetrics.has(bestKey) && dataPoint[bestKey] !== undefined) {{
                            const value = dataPoint[bestKey].toFixed(4);
                            html += `<p><span style="display:inline-block;width:10px;height:3px;background:${{color}};border-top:2px dashed ${{color}};margin-right:6px;"></span><strong>max ${{metric.replace(/_/g, ' ')}}:</strong> ${{value}}</p>`;
                        }}
                    }});

                    tooltip.innerHTML = html;
                    tooltip.style.display = 'block';
                    tooltip.style.left = (event.pageX + 15) + 'px';
                    tooltip.style.top = (event.pageY - 10) + 'px';
                }})
                .on('click', function(event) {{
                    // Find nearest data point and show program detail
                    const mouseX = d3.pointer(event)[0];
                    const targetOrder = Math.round(x.invert(mouseX));

                    let nearestPoint = null;
                    let minDist = Infinity;
                    chartData.forEach(d => {{
                        if (d._hidden) return;
                        const order = d.order !== undefined ? d.order : 0;
                        const dist = Math.abs(order - targetOrder);
                        if (dist < minDist) {{
                            minDist = dist;
                            nearestPoint = d;
                        }}
                    }});

                    if (nearestPoint && nearestPoint.program_id) {{
                        showProgramDetail(nearestPoint.program_id);
                    }}
                }});

            // Interactive legend at bottom
            const legendContainer = d3.select('#chart-container')
                .append('div')
                .style('display', 'flex')
                .style('flex-wrap', 'wrap')
                .style('justify-content', 'center')
                .style('gap', '10px')
                .style('margin-top', '10px');

            allMetrics.forEach((metric, idx) => {{
                const color = getMetricColor(metric, idx);
                const bestKey = 'best_' + metric;

                // Metric button
                const btn = legendContainer.append('div')
                    .style('display', 'flex')
                    .style('align-items', 'center')
                    .style('gap', '5px')
                    .style('padding', '4px 10px')
                    .style('background', visibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                    .style('border', '1px solid ' + (visibleMetrics.has(metric) ? color : '#30363d'))
                    .style('border-radius', '4px')
                    .style('cursor', 'pointer')
                    .style('font-size', '11px')
                    .style('color', visibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e')
                    .on('click', function() {{
                        if (visibleMetrics.has(metric)) {{
                            visibleMetrics.delete(metric);
                        }} else {{
                            visibleMetrics.add(metric);
                        }}
                        // Update button style
                        d3.select(this)
                            .style('background', visibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                            .style('border-color', visibleMetrics.has(metric) ? color : '#30363d')
                            .style('color', visibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e');
                        drawLines();
                    }});

                btn.append('div')
                    .style('width', '12px')
                    .style('height', '3px')
                    .style('background', color);

                btn.append('span').text(metric.replace(/_/g, ' '));

                // Best metric button
                const bestBtn = legendContainer.append('div')
                    .style('display', 'flex')
                    .style('align-items', 'center')
                    .style('gap', '5px')
                    .style('padding', '4px 10px')
                    .style('background', visibleMetrics.has(bestKey) ? '#21262d' : '#0d1117')
                    .style('border', '1px solid ' + (visibleMetrics.has(bestKey) ? color : '#30363d'))
                    .style('border-radius', '4px')
                    .style('cursor', 'pointer')
                    .style('font-size', '11px')
                    .style('color', visibleMetrics.has(bestKey) ? '#c9d1d9' : '#8b949e')
                    .on('click', function() {{
                        if (visibleMetrics.has(bestKey)) {{
                            visibleMetrics.delete(bestKey);
                        }} else {{
                            visibleMetrics.add(bestKey);
                        }}
                        d3.select(this)
                            .style('background', visibleMetrics.has(bestKey) ? '#21262d' : '#0d1117')
                            .style('border-color', visibleMetrics.has(bestKey) ? color : '#30363d')
                            .style('color', visibleMetrics.has(bestKey) ? '#c9d1d9' : '#8b949e');
                        drawLines();
                    }});

                bestBtn.append('div')
                    .style('width', '12px')
                    .style('height', '3px')
                    .style('background', color)
                    .style('border-top', '2px dashed ' + color);

                bestBtn.append('span').text('max ' + metric.replace(/_/g, ' '));
            }});
        }}

        // Render path analysis chart for selected program's ancestry
        function renderPathChart(ancestors) {{
            const container = document.getElementById('path-chart-container');
            container.innerHTML = '';

            if (!ancestors || ancestors.length < 2) {{
                container.innerHTML = '<p class="empty-state" style="margin: 0; padding: 20px;">Path too short for analysis</p>';
                return;
            }}

            // Extract path data from ancestors
            const pathData = ancestors.map((n, i) => {{
                const prog = programsData[n.data.id];
                return {{
                    step: i,
                    id: n.data.id,
                    label: n.data.order >= 0 ? `#${{n.data.order}}` : n.data.name,
                    metrics: prog ? prog.metrics : {{}},
                    mutation_summary: prog ? prog.mutation_summary : '',
                    fitness_delta: prog ? prog.fitness_delta : null
                }};
            }});

            // Get metrics to display (only *_score metrics)
            const scoreMetrics = Object.keys(pathData[0].metrics || {{}})
                .filter(k => k.endsWith('_score') && !k.startsWith('best_'));

            if (scoreMetrics.length === 0) {{
                container.innerHTML = '<p class="empty-state" style="margin: 0; padding: 20px;">No metrics available</p>';
                return;
            }}

            // Track visible metrics for this chart (default to function_score only)
            const pathVisibleMetrics = new Set(scoreMetrics.includes('fitness_score') ? ['fitness_score'] : (scoreMetrics.includes('function_score') ? ['function_score'] : scoreMetrics.slice(0, 1)));

            // Chart dimensions
            const width = container.clientWidth || 400;
            const height = 160;
            const margin = {{top: 15, right: 15, bottom: 35, left: 45}};

            const svg = d3.select('#path-chart-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            // X scale (steps along path)
            const x = d3.scaleLinear()
                .domain([0, pathData.length - 1])
                .range([margin.left, width - margin.right]);

            // Y scale (will be updated dynamically)
            const y = d3.scaleLinear()
                .range([height - margin.bottom, margin.top]);

            // Compute Y domain based on visible metrics
            function computeYDomain() {{
                const values = pathData.flatMap(d =>
                    Array.from(pathVisibleMetrics).map(m => d.metrics[m]).filter(v => typeof v === 'number')
                );
                if (values.length === 0) return [0, 1];
                const yMin = Math.min(...values);
                const yMax = Math.max(...values);
                const yPadding = (yMax - yMin) * 0.1 || 0.1;
                return [yMin - yPadding, yMax + yPadding];
            }}

            // Grid and axis groups
            const gridGroup = svg.append('g').attr('class', 'grid');
            const yAxisGroup = svg.append('g')
                .attr('class', 'y-axis')
                .attr('transform', `translate(${{margin.left}},0)`)
                .attr('color', '#8b949e');
            const linesGroup = svg.append('g').attr('class', 'lines');
            const pointsGroup = svg.append('g').attr('class', 'points');

            // X axis (static)
            svg.append('g')
                .attr('transform', `translate(0,${{height - margin.bottom}})`)
                .call(d3.axisBottom(x)
                    .ticks(Math.min(pathData.length, 10))
                    .tickFormat(i => {{
                        const d = pathData[Math.round(i)];
                        return d ? d.label : '';
                    }}))
                .attr('color', '#8b949e')
                .selectAll('text')
                .attr('font-size', '10px');

            // Hover interaction elements
            const focus = svg.append('g').style('display', 'none');

            // Vertical indicator line
            focus.append('line')
                .attr('class', 'hover-line')
                .attr('y1', margin.top)
                .attr('y2', height - margin.bottom)
                .attr('stroke', '#58a6ff')
                .attr('stroke-width', 1)
                .attr('stroke-dasharray', '4,4');

            // Draw function (called on initial render and when toggling metrics)
            function drawPathLines() {{
                // Update Y domain
                y.domain(computeYDomain());

                // Update grid
                gridGroup.selectAll('*').remove();
                gridGroup.attr('stroke', '#30363d').attr('stroke-opacity', 0.5)
                    .selectAll('line')
                    .data(y.ticks(4))
                    .join('line')
                    .attr('x1', margin.left)
                    .attr('x2', width - margin.right)
                    .attr('y1', d => y(d))
                    .attr('y2', d => y(d));

                // Update Y axis
                yAxisGroup.call(d3.axisLeft(y).ticks(4));

                // Clear and redraw lines
                linesGroup.selectAll('*').remove();
                pointsGroup.selectAll('*').remove();

                scoreMetrics.forEach((metric, idx) => {{
                    if (!pathVisibleMetrics.has(metric)) return;

                    const color = getMetricColor(metric, idx);
                    const validData = pathData.filter(d => typeof d.metrics[metric] === 'number');
                    if (validData.length < 2) return;

                    const line = d3.line()
                        .x(d => x(d.step))
                        .y(d => y(d.metrics[metric]));

                    // Visible line
                    linesGroup.append('path')
                        .datum(validData)
                        .attr('fill', 'none')
                        .attr('stroke', color)
                        .attr('stroke-width', 1.5)
                        .attr('d', line);

                    // Points (clickable)
                    pointsGroup.selectAll(`.path-point-${{idx}}`)
                        .data(validData)
                        .join('circle')
                        .attr('class', `path-point-${{idx}}`)
                        .attr('cx', d => x(d.step))
                        .attr('cy', d => y(d.metrics[metric]))
                        .attr('r', 4)
                        .attr('fill', color)
                        .attr('stroke', '#0d1117')
                        .attr('stroke-width', 1)
                        .style('cursor', 'pointer')
                        .on('click', (event, d) => showProgramDetail(d.id));
                }});
            }}

            // Transparent overlay to capture mouse events (added after drawPathLines is defined)
            svg.append('rect')
                .attr('class', 'path-chart-overlay')
                .attr('x', margin.left)
                .attr('y', margin.top)
                .attr('width', width - margin.left - margin.right)
                .attr('height', height - margin.top - margin.bottom)
                .style('fill', 'none')
                .style('pointer-events', 'all')
                .style('cursor', 'pointer')
                .on('mouseover', () => focus.style('display', null))
                .on('mouseout', () => {{
                    focus.style('display', 'none');
                    hideTooltip();
                }})
                .on('mousemove', function(event) {{
                    const mouseX = d3.pointer(event)[0];
                    const targetStep = Math.round(x.invert(mouseX));
                    const clampedStep = Math.max(0, Math.min(pathData.length - 1, targetStep));
                    const dataPoint = pathData[clampedStep];

                    if (!dataPoint) return;

                    // Update vertical line position
                    focus.select('.hover-line').attr('x1', x(clampedStep)).attr('x2', x(clampedStep));

                    // Build tooltip content showing all visible metrics
                    const tooltip = document.getElementById('tooltip');
                    let html = `<h4>${{dataPoint.label}}</h4>`;
                    html += `<p style="color: #8b949e; font-size: 0.85em;">ID: ${{dataPoint.id ? dataPoint.id.substring(0, 8) : '-'}}</p>`;
                    html += '<hr style="border-color: #30363d; margin: 8px 0;">';

                    // Show mutation summary if available
                    if (dataPoint.mutation_summary) {{
                        const deltaStr = dataPoint.fitness_delta !== null
                            ? ` (${{dataPoint.fitness_delta >= 0 ? '+' : ''}}${{(dataPoint.fitness_delta * 100).toFixed(1)}}%)`
                            : '';
                        html += `<p style="color: #a5d6ff; font-style: italic; margin-bottom: 8px;">"${{dataPoint.mutation_summary}}"${{deltaStr}}</p>`;
                    }}

                    // Show values for all visible metrics
                    scoreMetrics.forEach((metric, idx) => {{
                        if (!pathVisibleMetrics.has(metric)) return;
                        const color = getMetricColor(metric, idx);
                        const value = dataPoint.metrics[metric];
                        if (typeof value === 'number') {{
                            html += `<p><span style="display:inline-block;width:10px;height:10px;background:${{color}};border-radius:2px;margin-right:6px;"></span><strong>${{metric.replace(/_/g, ' ')}}:</strong> ${{value.toFixed(4)}}</p>`;
                        }}
                    }});

                    tooltip.innerHTML = html;
                    tooltip.style.display = 'block';
                    tooltip.style.left = (event.pageX + 15) + 'px';
                    tooltip.style.top = (event.pageY - 10) + 'px';
                }})
                .on('click', function(event) {{
                    const mouseX = d3.pointer(event)[0];
                    const targetStep = Math.round(x.invert(mouseX));
                    const clampedStep = Math.max(0, Math.min(pathData.length - 1, targetStep));
                    const dataPoint = pathData[clampedStep];

                    if (dataPoint && dataPoint.id) {{
                        showProgramDetail(dataPoint.id);
                    }}
                }});

            // Initial draw
            drawPathLines();

            // Interactive legend
            const legendContainer = d3.select('#path-chart-container')
                .append('div')
                .style('display', 'flex')
                .style('flex-wrap', 'wrap')
                .style('justify-content', 'center')
                .style('gap', '8px')
                .style('margin-top', '8px');

            scoreMetrics.forEach((metric, idx) => {{
                const color = getMetricColor(metric, idx);

                const btn = legendContainer.append('div')
                    .style('display', 'flex')
                    .style('align-items', 'center')
                    .style('gap', '5px')
                    .style('padding', '4px 10px')
                    .style('background', pathVisibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                    .style('border', '1px solid ' + (pathVisibleMetrics.has(metric) ? color : '#30363d'))
                    .style('border-radius', '4px')
                    .style('cursor', 'pointer')
                    .style('font-size', '11px')
                    .style('color', pathVisibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e')
                    .on('click', function() {{
                        if (pathVisibleMetrics.has(metric)) {{
                            pathVisibleMetrics.delete(metric);
                        }} else {{
                            pathVisibleMetrics.add(metric);
                        }}
                        d3.select(this)
                            .style('background', pathVisibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                            .style('border-color', pathVisibleMetrics.has(metric) ? color : '#30363d')
                            .style('color', pathVisibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e');
                        drawPathLines();
                    }});

                btn.append('div')
                    .style('width', '12px')
                    .style('height', '3px')
                    .style('background', color);

                btn.append('span').text(metric.replace(/_/g, ' '));
            }});
        }}

        // Render evolution tree
        function renderTree() {{
            const container = document.getElementById('tree-container');
            const width = container.clientWidth;

            // Calculate tree dimensions
            const nodeCount = countNodes(treeData);
            const height = Math.max(500, nodeCount * 30);

            const svg = d3.select('#tree-svg')
                .attr('width', width)
                .attr('height', height);

            if (!treeData.id || treeData.id === 'empty') {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No evolution tree data available');
                return;
            }}

            const margin = {{top: 40, right: 120, bottom: 40, left: 120}};

            const g = svg.append('g')
                .attr('transform', `translate(${{margin.left}},${{margin.top}})`);

            const treeLayout = d3.tree()
                .size([width - margin.left - margin.right, height - margin.top - margin.bottom]);

            const root = d3.hierarchy(treeData);
            treeRoot = root;  // Store for ancestry path lookup
            treeLayout(root);

            // Links
            g.selectAll('.link')
                .data(root.links())
                .join('path')
                .attr('class', 'link')
                .attr('d', d3.linkVertical()
                    .x(d => d.x)
                    .y(d => d.y));

            // Find best program based on selected metric
            const currentBestId = findBestProgramId();

            // Nodes
            const nodes = g.selectAll('.node')
                .data(root.descendants())
                .join('g')
                .attr('class', d => `node ${{d.data.id === currentBestId ? 'best' : ''}}`)
                .attr('transform', d => `translate(${{d.x}},${{d.y}})`);

            // Helper to check if node evaluation failed
            function isFailed(d) {{
                return d.data.has_error || !d.data.metrics;
            }}

            // Circles - bind click events directly to circles
            nodes.append('circle')
                .attr('r', d => d.data.id === currentBestId ? 10 : 7)
                .attr('fill', d => {{
                    if (isFailed(d)) return '#6e7681';  // Gray for failed
                    const score = getScore(d.data);
                    return safeColor(score);
                }})
                .attr('stroke', d => {{
                    if (d.data.id === currentBestId) return '#ffd700';  // Gold for best
                    if (isFailed(d)) return '#f85149';  // Red for failed
                    const color = safeColor(getScore(d.data));
                    const darkerColor = d3.color(color);
                    return darkerColor ? darkerColor.darker() : color;
                }})
                .attr('stroke-dasharray', d => isFailed(d) ? '3,2' : 'none')
                .attr('stroke-width', d => isFailed(d) ? 2 : 1.5)
                .style('cursor', 'pointer')
                .style('pointer-events', 'all')
                .on('click', function(event, d) {{
                    event.stopPropagation();
                    showProgramDetail(d.data.id);
                }})
                .on('mouseover', function(event, d) {{
                    showTooltip(event, d.data);
                }})
                .on('mouseout', hideTooltip);

            nodes.append('text')
                .attr('dy', -12)
                .attr('text-anchor', 'middle')
                .style('pointer-events', 'none')
                .text(d => d.data.iteration >= 0 ? `#${{d.data.iteration}}` : d.data.name);
        }}

        function countNodes(node) {{
            let count = 1;
            if (node.children) {{
                for (const child of node.children) {{
                    count += countNodes(child);
                }}
            }}
            return count;
        }}

        // Find node by ID in D3 hierarchy
        function findNodeById(node, id) {{
            if (node.data.id === id) return node;
            if (node.children) {{
                for (const child of node.children) {{
                    const found = findNodeById(child, id);
                    if (found) return found;
                }}
            }}
            return null;
        }}

        // Tooltip functions
        function showTooltip(event, data) {{
            const tooltip = document.getElementById('tooltip');
            // Check if evaluation failed
            const evalFailed = data.has_error || !data.metrics;
            // Build metrics HTML
            let metricsHtml = '';
            if (data.metrics && Object.keys(data.metrics).length > 0) {{
                metricsHtml = '<hr style="border-color: #30363d; margin: 8px 0;">';
                for (const [key, value] of Object.entries(data.metrics)) {{
                    const displayValue = typeof value === 'number' ? value.toFixed(4) : value;
                    // Highlight the currently selected metric
                    const isSelected = key === selectedMetric;
                    const style = isSelected ? 'color: #58a6ff;' : '';
                    metricsHtml += `<p style="${{style}}"><strong>${{key}}:</strong> ${{displayValue}}</p>`;
                }}
            }}
            // Check if this is the best program for the selected metric
            const isBest = data.id === findBestProgramId();
            const orderStr = data.order !== undefined && data.order >= 0 ? '#' + data.order : '';
            tooltip.innerHTML = `
                <h4>Program ${{orderStr}}: ${{data.name}}</h4>
                ${{data.order !== undefined && data.order >= 0 ? '<p><strong>Order:</strong> ' + data.order + '</p>' : ''}}
                <p><strong>Generation:</strong> ${{data.generation}}</p>
                <p><strong>Island:</strong> ${{data.island_id}}</p>
                ${{evalFailed ? '<p style="color: #f85149;"><strong>⚠ Evaluation Failed</strong></p>' : ''}}
                ${{isBest ? '<p style="color: #ffd700;"><strong>★ Best for ' + selectedMetric.replace(/_/g, ' ') + '</strong></p>' : ''}}
                ${{metricsHtml}}
            `;
            tooltip.style.display = 'block';
            tooltip.style.left = (event.pageX + 10) + 'px';
            tooltip.style.top = (event.pageY + 10) + 'px';
        }}

        function hideTooltip() {{
            document.getElementById('tooltip').style.display = 'none';
        }}

        // Currently selected node
        let selectedNodeId = null;
        let currentFullPath = [];  // Store full path for navigation persistence

        // Show program detail panel
        function showProgramDetail(programId) {{
            console.log('showProgramDetail called with:', programId);

            try {{
                const program = programsData[programId];
                if (!program) {{
                    console.warn('Program not found:', programId);
                    return;
                }}
                console.log('Program found:', program.id);

                // Update selected node highlight
                if (selectedNodeId !== programId) {{
                    // Remove highlight from previous node
                    d3.selectAll('.node circle').attr('stroke-width', 2);
                    // Add highlight to new node
                    d3.selectAll('.node').each(function(d) {{
                        if (d && d.data && d.data.id === programId) {{
                            d3.select(this).select('circle').attr('stroke-width', 4);
                        }}
                    }});
                    selectedNodeId = programId;
                }}

                // Update selection line on score history chart
                if (chartContext && program.order !== undefined) {{
                    const xPos = chartContext.x(program.order);
                    chartContext.selectionLine
                        .attr('x1', xPos)
                        .attr('x2', xPos)
                        .style('display', null);
                }}

                const panel = document.getElementById('detail-panel');
                panel.classList.add('active');
                console.log('Panel activated');

                const orderPart = program.order !== undefined && program.order >= 0 ? `#${{program.order}} ` : '';
                document.getElementById('detail-title').textContent =
                    `Program ${{orderPart}}(${{programId.substring(0, 8)}})${{program.is_best ? ' ★ Best' : ''}}`;

                // Build ancestry path
                const pathContainer = document.getElementById('ancestry-path');
                // Clear previous path highlighting
                d3.selectAll('.node').classed('on-path', false);
                d3.selectAll('.link').classed('on-path', false);

                if (treeRoot) {{
                    const node = findNodeById(treeRoot, programId);
                    if (node) {{
                        const ancestors = node.ancestors().reverse();  // Root to current
                        const newPathIds = ancestors.map(n => n.data.id);

                        // Check if clicked node is in current path
                        const indexInCurrentPath = currentFullPath.indexOf(programId);
                        if (indexInCurrentPath === -1) {{
                            // Not in current path, rebuild full path
                            currentFullPath = newPathIds;
                        }}
                        // Otherwise keep currentFullPath unchanged for easy comparison

                        // Render path with current/future styling
                        const currentIndex = currentFullPath.indexOf(programId);
                        pathContainer.innerHTML = currentFullPath.map((nodeId, i) => {{
                            const nodeData = findNodeById(treeRoot, nodeId);
                            if (!nodeData) return '';
                            const order = nodeData.data.order !== undefined ? nodeData.data.order : nodeData.data.iteration;
                            const label = order >= 0 ? `#${{order}}` : nodeData.data.name;
                            const isCurrent = i === currentIndex;
                            const isFuture = i > currentIndex;
                            const nodeClass = isCurrent ? ' current' : (isFuture ? ' future' : '');
                            const sepClass = isFuture ? ' future' : '';
                            const nodeHtml = `<span class="path-node${{nodeClass}}" onclick="showProgramDetail('${{nodeId}}')">${{label}}</span>`;
                            return i === currentFullPath.length - 1 ? nodeHtml : nodeHtml + `<span class="path-separator${{sepClass}}">→</span>`;
                        }}).join('');

                        // Highlight nodes and links on path in the tree (use ancestors for highlighting)
                        const ancestorIds = new Set(ancestors.map(n => n.data.id));

                        d3.selectAll('.node').each(function(d) {{
                            if (d && d.data && ancestorIds.has(d.data.id)) {{
                                d3.select(this).classed('on-path', true);
                            }}
                        }});

                        d3.selectAll('.link').each(function(d) {{
                            if (d && d.source && d.target) {{
                                const sourceOnPath = ancestorIds.has(d.source.data.id);
                                const targetOnPath = ancestorIds.has(d.target.data.id);
                                if (sourceOnPath && targetOnPath) {{
                                    d3.select(this).classed('on-path', true);
                                }}
                            }}
                        }});

                        // Render path analysis chart
                        renderPathChart(ancestors);
                    }} else {{
                        pathContainer.innerHTML = '';
                        document.getElementById('path-chart-container').innerHTML = '';
                    }}
                }} else {{
                    pathContainer.innerHTML = '';
                    document.getElementById('path-chart-container').innerHTML = '';
                }}

                // Metrics
                const metricsHtml = Object.entries(program.metrics)
                    .map(([key, value]) => `
                        <div class="metric-item">
                            <div class="metric-label">${{key}}</div>
                            <div class="metric-value">${{typeof value === 'number' ? value.toFixed(4) : value}}</div>
                        </div>
                    `).join('');
                document.getElementById('detail-metrics').innerHTML = metricsHtml;

                // Diff view - check if diff2html is available
                const diffView = document.getElementById('diff-view');
                if (program.diff && program.diff.trim()) {{
                    if (typeof Diff2HtmlUI !== 'undefined') {{
                        try {{
                            const diff2htmlUi = new Diff2HtmlUI(diffView, program.diff, {{
                                drawFileList: false,
                                matching: 'lines',
                                outputFormat: 'side-by-side',
                            }});
                            diff2htmlUi.draw();
                            // Apply syntax highlighting if highlight.js is available
                            if (typeof hljs !== 'undefined') {{
                                diff2htmlUi.highlightCode();
                            }}
                        }} catch (e) {{
                            console.warn('diff2html error:', e);
                            diffView.innerHTML = `<pre style="padding: 15px; background: #0d1117; overflow-x: auto; white-space: pre-wrap;">${{escapeHtml(program.diff)}}</pre>`;
                        }}
                    }} else {{
                        console.warn('Diff2HtmlUI not available, using plain text');
                        diffView.innerHTML = `<pre style="padding: 15px; background: #0d1117; overflow-x: auto; white-space: pre-wrap;">${{escapeHtml(program.diff)}}</pre>`;
                    }}
                }} else {{
                    diffView.innerHTML = '<p class="empty-state">No diff available (initial program or unchanged)</p>';
                }}

                // Feedback view - show summary, issues, and suggestions
                let feedbackHtml = '';

                if (program.llm_feedback) {{
                    feedbackHtml += `<h4 style="color: #58a6ff; margin-bottom: 10px; margin-top: 0;">Summary</h4>`;
                    feedbackHtml += `<p style="margin-bottom: 20px;">${{escapeHtml(program.llm_feedback)}}</p>`;
                }}

                if (program.issues && program.issues.length > 0) {{
                    feedbackHtml += `<h4 style="color: #f85149; margin-bottom: 10px;">Issues Found (${{program.issues.length}})</h4>`;
                    feedbackHtml += '<ul style="margin-bottom: 20px; padding-left: 20px;">';
                    program.issues.forEach(issue => {{
                        feedbackHtml += `<li style="margin-bottom: 8px;">${{escapeHtml(issue)}}</li>`;
                    }});
                    feedbackHtml += '</ul>';
                }}

                if (program.suggestions && program.suggestions.length > 0) {{
                    feedbackHtml += `<h4 style="color: #3fb950; margin-bottom: 10px;">Suggestions (${{program.suggestions.length}})</h4>`;
                    feedbackHtml += '<ul style="margin-bottom: 20px; padding-left: 20px;">';
                    program.suggestions.forEach(s => {{
                        feedbackHtml += `<li style="margin-bottom: 8px;">${{escapeHtml(s)}}</li>`;
                    }});
                    feedbackHtml += '</ul>';
                }}

                if (!feedbackHtml) {{
                    feedbackHtml = '<p class="empty-state">No LLM feedback available</p>';
                }}

                document.getElementById('feedback-view').innerHTML = feedbackHtml;

                // Analysis view - render analyzer's analysis content as markdown
                let analysisHtml = '';
                if (program.analysis_used && program.analysis_used.trim()) {{
                    // Use marked.js to render markdown content
                    if (typeof marked !== 'undefined') {{
                        analysisHtml = `<div class="analysis-content markdown-body">${{marked.parse(program.analysis_used)}}</div>`;
                    }} else {{
                        // Fallback to plain text if marked is not available
                        analysisHtml = `<div class="analysis-content" style="white-space: pre-wrap;">${{escapeHtml(program.analysis_used)}}</div>`;
                    }}
                }} else {{
                    analysisHtml = '<p class="empty-state">No analysis available</p>';
                }}
                document.getElementById('analysis-view').innerHTML = analysisHtml;

                // Apply syntax highlighting to code blocks in analysis
                document.querySelectorAll('#analysis-view pre code').forEach(block => {{
                    if (typeof hljs !== 'undefined') {{
                        hljs.highlightElement(block);
                    }}
                }});

                // Prompt view - render prompts with sub-tab switching
                // Store current program for prompt switching
                window.currentPromptProgram = program;

                // Render the currently selected prompt type
                const activeSubtab = document.querySelector('.prompt-subtab.active');
                const promptType = activeSubtab ? activeSubtab.dataset.prompt : 'analyzer';
                renderPromptContent(promptType);

                // Set up sub-tab click handlers
                document.querySelectorAll('.prompt-subtab').forEach(btn => {{
                    btn.onclick = function() {{
                        document.querySelectorAll('.prompt-subtab').forEach(b => b.classList.remove('active'));
                        this.classList.add('active');
                        renderPromptContent(this.dataset.prompt);
                    }};
                }});

                // Code preview with multi-file support
                const codeContainer = document.getElementById('code-files-container');
                codeContainer.innerHTML = '';

                const files = program.code_files || {{}};
                const sortedPaths = Object.keys(files).sort();

                if (sortedPaths.length === 0) {{
                    codeContainer.innerHTML = '<p class="empty-state">No code files available</p>';
                }} else {{
                    sortedPaths.forEach((path, idx) => {{
                        const content = files[path];
                        const lines = content.split('\\n');
                        const lineNums = lines.map((_, i) => i + 1).join('<br>');

                        const fileSection = document.createElement('div');
                        fileSection.className = 'file-section';
                        fileSection.innerHTML = `
                            <div class="file-header" onclick="this.parentElement.classList.toggle('collapsed')">
                                <span class="collapse-icon">▼</span>
                                <span class="file-path">${{path}}</span>
                                <span class="file-stats">${{lines.length}} lines</span>
                                <div class="file-actions">
                                    <button onclick="event.stopPropagation(); copyFileContent(this, '${{path.replace(/'/g, "\\'")}}')">Copy</button>
                                </div>
                            </div>
                            <div class="file-content">
                                <div class="line-numbers">${{lineNums}}</div>
                                <pre><code class="language-python">${{escapeHtml(content)}}</code></pre>
                            </div>
                        `;
                        codeContainer.appendChild(fileSection);

                        // Store content for copy functionality
                        fileSection.dataset.content = content;

                        // Apply syntax highlighting
                        const codeEl = fileSection.querySelector('code');
                        if (typeof hljs !== 'undefined') {{
                            hljs.highlightElement(codeEl);
                        }}
                    }});
                }}

                // Scroll to panel
                panel.scrollIntoView({{ behavior: 'smooth' }});
                console.log('showProgramDetail completed successfully');

            }} catch (e) {{
                console.error('Error in showProgramDetail:', e);
                // Show error to user
                const panel = document.getElementById('detail-panel');
                panel.classList.add('active');
                document.getElementById('detail-title').textContent = 'Error';
                document.getElementById('detail-metrics').innerHTML =
                    `<div class="empty-state" style="color: #f85149;">Error: ${{e.message}}</div>`;
            }}
        }}

        // Render prompt content based on selected type
        function renderPromptContent(promptType) {{
            const program = window.currentPromptProgram;
            if (!program) return;

            let promptText = '';
            let emptyMessage = '';

            if (promptType === 'analyzer') {{
                promptText = program.analysis_prompt_used || '';
                emptyMessage = 'No analyzer prompt available (initial program or analyzer disabled)';
            }} else {{
                promptText = program.mutator_prompt_used || '';
                emptyMessage = 'No mutator prompt available (initial program)';
            }}

            let promptHtml = '';
            if (promptText && promptText.trim()) {{
                if (typeof marked !== 'undefined') {{
                    promptHtml = `<div class="prompt-content markdown-body">${{marked.parse(promptText)}}</div>`;
                }} else {{
                    promptHtml = `<div class="prompt-content" style="white-space: pre-wrap;">${{escapeHtml(promptText)}}</div>`;
                }}
            }} else {{
                promptHtml = `<p class="empty-state">${{emptyMessage}}</p>`;
            }}

            document.getElementById('prompt-view').innerHTML = promptHtml;

            // Apply syntax highlighting to code blocks
            document.querySelectorAll('#prompt-view pre code').forEach(block => {{
                if (typeof hljs !== 'undefined') {{
                    hljs.highlightElement(block);
                }}
            }});
        }}

        // Helper function to escape HTML
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}

        // Copy file content to clipboard
        function copyFileContent(btn, path) {{
            const fileSection = btn.closest('.file-section');
            const content = fileSection.dataset.content;
            navigator.clipboard.writeText(content).then(() => {{
                btn.textContent = 'Copied!';
                setTimeout(() => {{ btn.textContent = 'Copy'; }}, 2000);
            }}).catch(err => {{
                console.error('Failed to copy:', err);
                btn.textContent = 'Failed';
                setTimeout(() => {{ btn.textContent = 'Copy'; }}, 2000);
            }});
        }}

        function closeDetailPanel() {{
            document.getElementById('detail-panel').classList.remove('active');
            // Clear selection highlight
            d3.selectAll('.node circle').attr('stroke-width', 2);
            selectedNodeId = null;
            currentFullPath = [];  // Clear saved path
            // Clear path highlighting
            d3.selectAll('.node').classed('on-path', false);
            d3.selectAll('.link').classed('on-path', false);
            // Hide selection line on score history chart
            if (chartContext && chartContext.selectionLine) {{
                chartContext.selectionLine.style('display', 'none');
            }}
            // Clear path analysis chart
            document.getElementById('path-chart-container').innerHTML = '';
        }}

        // Tree Explorer Modal Functions
        let explorerSelectedId = null;
        let explorerTreeRoot = null;

        function openTreeExplorer() {{
            const modal = document.getElementById('tree-explorer-modal');
            modal.classList.add('active');

            // Disable body scroll to prevent scroll leakage
            document.body.style.overflow = 'hidden';

            // Setup resizable panels
            setupExplorerResizer();

            // Render tree in explorer
            renderExplorerTree();

            // If a node is selected in main view, show its detail
            if (selectedNodeId) {{
                showExplorerDetail(selectedNodeId);
            }}

            // ESC to close
            document.addEventListener('keydown', handleExplorerEsc);
        }}

        function closeTreeExplorer() {{
            const modal = document.getElementById('tree-explorer-modal');
            modal.classList.remove('active');
            document.removeEventListener('keydown', handleExplorerEsc);

            // Restore body scroll
            document.body.style.overflow = '';
        }}

        function handleExplorerEsc(e) {{
            if (e.key === 'Escape') {{
                closeTreeExplorer();
            }}
        }}

        function renderExplorerTree() {{
            const container = document.getElementById('explorer-tree-container');
            container.innerHTML = '';

            const width = container.clientWidth || 800;
            const nodeCount = countNodes(treeData);
            const height = Math.max(600, nodeCount * 35);

            const svg = d3.select('#explorer-tree-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            if (!treeData.id || treeData.id === 'empty') {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No evolution tree data available');
                return;
            }}

            const margin = {{top: 40, right: 120, bottom: 40, left: 120}};

            const g = svg.append('g')
                .attr('transform', `translate(${{margin.left}},${{margin.top}})`);

            const treeLayout = d3.tree()
                .size([width - margin.left - margin.right, height - margin.top - margin.bottom]);

            const root = d3.hierarchy(treeData);
            explorerTreeRoot = root;
            treeLayout(root);

            // Links
            g.selectAll('.link')
                .data(root.links())
                .join('path')
                .attr('class', 'link')
                .attr('d', d3.linkVertical()
                    .x(d => d.x)
                    .y(d => d.y));

            // Find best program
            const currentBestId = findBestProgramId();

            // Helper to check if node evaluation failed
            function isFailed(d) {{
                return d.data.has_error || !d.data.metrics;
            }}

            // Nodes
            const nodes = g.selectAll('.node')
                .data(root.descendants())
                .join('g')
                .attr('class', d => {{
                    let cls = 'node';
                    if (d.data.id === currentBestId) cls += ' best';
                    if (d.data.id === explorerSelectedId) cls += ' selected';
                    return cls;
                }})
                .attr('transform', d => `translate(${{d.x}},${{d.y}})`);

            // Circles
            nodes.append('circle')
                .attr('r', d => {{
                    if (d.data.id === explorerSelectedId) return 10;
                    if (d.data.id === currentBestId) return 10;
                    return 7;
                }})
                .attr('fill', d => {{
                    if (isFailed(d)) return '#6e7681';
                    const score = getScore(d.data);
                    return safeColor(score);
                }})
                .attr('stroke', d => {{
                    if (d.data.id === explorerSelectedId) return '#58a6ff';
                    if (d.data.id === currentBestId) return '#ffd700';
                    if (isFailed(d)) return '#f85149';
                    const color = safeColor(getScore(d.data));
                    const darkerColor = d3.color(color);
                    return darkerColor ? darkerColor.darker() : color;
                }})
                .attr('stroke-dasharray', d => isFailed(d) ? '3,2' : 'none')
                .attr('stroke-width', d => {{
                    if (d.data.id === explorerSelectedId) return 3;
                    if (isFailed(d)) return 2;
                    return 1.5;
                }})
                .style('cursor', 'pointer')
                .style('pointer-events', 'all')
                .on('click', function(event, d) {{
                    event.stopPropagation();
                    showExplorerDetail(d.data.id);
                    updateExplorerHighlight(d.data.id);
                }})
                .on('mouseover', function(event, d) {{
                    showTooltip(event, d.data);
                }})
                .on('mouseout', hideTooltip);

            // Labels
            nodes.append('text')
                .attr('dy', -12)
                .attr('text-anchor', 'middle')
                .style('pointer-events', 'none')
                .text(d => d.data.iteration >= 0 ? `#${{d.data.iteration}}` : d.data.name);

            // Highlight path to selected node
            if (explorerSelectedId && explorerTreeRoot) {{
                const selectedNode = findNodeById(explorerTreeRoot, explorerSelectedId);
                if (selectedNode) {{
                    const ancestors = selectedNode.ancestors();
                    const ancestorIds = new Set(ancestors.map(n => n.data.id));

                    // Highlight nodes on path
                    g.selectAll('.node').each(function(d) {{
                        if (d && d.data && ancestorIds.has(d.data.id)) {{
                            d3.select(this).classed('on-path', true);
                        }}
                    }});

                    // Highlight links on path
                    g.selectAll('.link').each(function(d) {{
                        if (d && d.source && d.target) {{
                            const sourceOnPath = ancestorIds.has(d.source.data.id);
                            const targetOnPath = ancestorIds.has(d.target.data.id);
                            if (sourceOnPath && targetOnPath) {{
                                d3.select(this).classed('on-path', true);
                            }}
                        }}
                    }});
                }}
            }}
        }}

        let explorerFullPath = [];  // Store full path for explorer navigation

        function showExplorerDetail(programId) {{
            const panel = document.getElementById('explorer-detail-panel');
            const program = programsData[programId];

            if (!program) {{
                panel.innerHTML = '<p class="explorer-empty-state">Program not found</p>';
                return;
            }}

            const orderPart = program.order !== undefined && program.order >= 0 ? `#${{program.order}} ` : '';
            const bestBadge = program.is_best ? ' <span style="color: #ffd700;">★ Best</span>' : '';

            let html = `
                <div class="explorer-detail-title">
                    Program ${{orderPart}}(${{programId.substring(0, 8)}})${{bestBadge}}
                </div>
            `;

            // Build ancestry path
            html += `<div class="ancestry-path" id="explorer-ancestry-path"></div>`;

            // Path analysis
            html += `
                <div class="path-analysis" id="explorer-path-analysis">
                    <div class="path-analysis-header">
                        <span>Path Analysis</span>
                    </div>
                    <div id="explorer-path-chart-container"></div>
                </div>
            `;

            // Mutation summary
            if (program.mutation_summary) {{
                const deltaText = program.fitness_delta !== undefined && program.fitness_delta !== null
                    ? ` (<span style="color: ${{program.fitness_delta >= 0 ? '#3fb950' : '#f85149'}};">${{program.fitness_delta >= 0 ? '+' : ''}}${{(program.fitness_delta * 100).toFixed(2)}}%</span>)`
                    : '';
                html += `
                    <div class="explorer-mutation-summary">
                        <strong>Mutation:</strong> ${{escapeHtml(program.mutation_summary)}}${{deltaText}}
                    </div>
                `;
            }}

            // Metrics
            const metricsHtml = Object.entries(program.metrics)
                .map(([key, value]) => `
                    <div class="metric-item">
                        <div class="metric-label">${{key}}</div>
                        <div class="metric-value">${{typeof value === 'number' ? value.toFixed(4) : value}}</div>
                    </div>
                `).join('');

            html += `
                <div class="metrics-grid" style="padding: 15px 20px;">
                    ${{metricsHtml}}
                </div>
            `;

            // Tabs - same as main panel
            html += `
                <div class="tabs">
                    <div class="tab active" data-explorer-tab="diff">Diff</div>
                    <div class="tab" data-explorer-tab="analysis">Analysis</div>
                    <div class="tab" data-explorer-tab="prompt">Prompt</div>
                    <div class="tab" data-explorer-tab="feedback">LLM Feedback</div>
                    <div class="tab" data-explorer-tab="code">Code Preview</div>
                </div>
            `;

            // Tab contents - Diff tab
            const diffContent = program.diff && program.diff.trim()
                ? `<div class="diff-container" id="explorer-diff-view"></div>`
                : '<p class="empty-state">No diff available (initial program or unchanged)</p>';
            html += `<div class="tab-content active" id="explorer-tab-diff">${{diffContent}}</div>`;

            // Analysis tab
            html += `<div class="tab-content" id="explorer-tab-analysis"><div id="explorer-analysis-view"></div></div>`;

            // Prompt tab with sub-tabs
            html += `
                <div class="tab-content" id="explorer-tab-prompt">
                    <div class="prompt-subtabs">
                        <button class="prompt-subtab active" data-explorer-prompt="analyzer">Analyzer Prompt</button>
                        <button class="prompt-subtab" data-explorer-prompt="mutator">Mutator Prompt</button>
                    </div>
                    <div id="explorer-prompt-view"></div>
                </div>
            `;

            // LLM Feedback tab
            html += `<div class="tab-content" id="explorer-tab-feedback"><div id="explorer-feedback-view"></div></div>`;

            // Code tab
            html += `<div class="tab-content" id="explorer-tab-code"><div id="explorer-code-container"></div></div>`;

            panel.innerHTML = html;

            // Build and render ancestry path
            if (explorerTreeRoot) {{
                const node = findNodeById(explorerTreeRoot, programId);
                if (node) {{
                    const ancestors = node.ancestors().reverse();  // Root to current
                    const newPathIds = ancestors.map(n => n.data.id);

                    // Check if clicked node is in current path
                    const indexInCurrentPath = explorerFullPath.indexOf(programId);
                    if (indexInCurrentPath === -1) {{
                        explorerFullPath = newPathIds;
                    }}

                    // Render path with current/future styling
                    const pathContainer = document.getElementById('explorer-ancestry-path');
                    const currentIndex = explorerFullPath.indexOf(programId);
                    pathContainer.innerHTML = explorerFullPath.map((nodeId, i) => {{
                        const nodeData = findNodeById(explorerTreeRoot, nodeId);
                        if (!nodeData) return '';
                        const order = nodeData.data.order !== undefined ? nodeData.data.order : nodeData.data.iteration;
                        const label = order >= 0 ? `#${{order}}` : nodeData.data.name;
                        const isCurrent = i === currentIndex;
                        const isFuture = i > currentIndex;
                        const nodeClass = isCurrent ? ' current' : (isFuture ? ' future' : '');
                        const sepClass = isFuture ? ' future' : '';
                        const nodeHtml = `<span class="path-node${{nodeClass}}" onclick="explorerNavigateTo('${{nodeId}}')">${{label}}</span>`;
                        return i === explorerFullPath.length - 1 ? nodeHtml : nodeHtml + `<span class="path-separator${{sepClass}}">→</span>`;
                    }}).join('');

                    // Render path analysis chart
                    renderExplorerPathChart(ancestors);
                }}
            }}

            // Render diff with diff2html
            if (program.diff && program.diff.trim()) {{
                const diffView = document.getElementById('explorer-diff-view');
                if (typeof Diff2HtmlUI !== 'undefined') {{
                    try {{
                        const diff2htmlUi = new Diff2HtmlUI(diffView, program.diff, {{
                            drawFileList: false,
                            matching: 'lines',
                            outputFormat: 'side-by-side',
                        }});
                        diff2htmlUi.draw();
                        if (typeof hljs !== 'undefined') {{
                            diff2htmlUi.highlightCode();
                        }}
                    }} catch (e) {{
                        diffView.innerHTML = `<pre style="padding: 15px; background: #0d1117; overflow-x: auto; white-space: pre-wrap;">${{escapeHtml(program.diff)}}</pre>`;
                    }}
                }} else {{
                    diffView.innerHTML = `<pre style="padding: 15px; background: #0d1117; overflow-x: auto; white-space: pre-wrap;">${{escapeHtml(program.diff)}}</pre>`;
                }}
            }}

            // Render analysis with markdown
            let analysisHtml = '';
            if (program.analysis_used && program.analysis_used.trim()) {{
                if (typeof marked !== 'undefined') {{
                    analysisHtml = `<div class="analysis-content markdown-body">${{marked.parse(program.analysis_used)}}</div>`;
                }} else {{
                    analysisHtml = `<div class="analysis-content" style="white-space: pre-wrap;">${{escapeHtml(program.analysis_used)}}</div>`;
                }}
            }} else {{
                analysisHtml = '<p class="empty-state">No analysis available</p>';
            }}
            document.getElementById('explorer-analysis-view').innerHTML = analysisHtml;

            // Apply syntax highlighting to code blocks in analysis
            document.querySelectorAll('#explorer-analysis-view pre code').forEach(block => {{
                if (typeof hljs !== 'undefined') {{
                    hljs.highlightElement(block);
                }}
            }});

            // Render LLM feedback
            let feedbackHtml = '';
            if (program.llm_feedback) {{
                feedbackHtml += `<h4 style="color: #58a6ff; margin-bottom: 10px; margin-top: 0;">Summary</h4>`;
                feedbackHtml += `<p style="margin-bottom: 20px;">${{escapeHtml(program.llm_feedback)}}</p>`;
            }}
            if (program.issues && program.issues.length > 0) {{
                feedbackHtml += `<h4 style="color: #f85149; margin-bottom: 10px;">Issues Found (${{program.issues.length}})</h4>`;
                feedbackHtml += '<ul style="margin-bottom: 20px; padding-left: 20px;">';
                program.issues.forEach(issue => {{
                    feedbackHtml += `<li style="margin-bottom: 8px;">${{escapeHtml(issue)}}</li>`;
                }});
                feedbackHtml += '</ul>';
            }}
            if (program.suggestions && program.suggestions.length > 0) {{
                feedbackHtml += `<h4 style="color: #3fb950; margin-bottom: 10px;">Suggestions (${{program.suggestions.length}})</h4>`;
                feedbackHtml += '<ul style="margin-bottom: 20px; padding-left: 20px;">';
                program.suggestions.forEach(s => {{
                    feedbackHtml += `<li style="margin-bottom: 8px;">${{escapeHtml(s)}}</li>`;
                }});
                feedbackHtml += '</ul>';
            }}
            if (!feedbackHtml) {{
                feedbackHtml = '<p class="empty-state">No LLM feedback available</p>';
            }}
            document.getElementById('explorer-feedback-view').innerHTML = feedbackHtml;

            // Render code files
            const codeContainer = document.getElementById('explorer-code-container');
            const files = program.code_files || {{}};
            const sortedPaths = Object.keys(files).sort();

            if (sortedPaths.length === 0) {{
                codeContainer.innerHTML = '<p class="empty-state">No code files available</p>';
            }} else {{
                sortedPaths.forEach(path => {{
                    const content = files[path];
                    const lines = content.split('\\n');
                    const lineNums = lines.map((_, i) => i + 1).join('<br>');
                    const fileSection = document.createElement('div');
                    fileSection.className = 'file-section';
                    fileSection.innerHTML = `
                        <div class="file-header" onclick="this.parentElement.classList.toggle('collapsed')">
                            <span class="collapse-icon">▼</span>
                            <span class="file-path">${{path}}</span>
                            <span class="file-stats">${{lines.length}} lines</span>
                        </div>
                        <div class="file-content">
                            <div class="line-numbers">${{lineNums}}</div>
                            <pre><code class="language-python">${{escapeHtml(content)}}</code></pre>
                        </div>
                    `;
                    codeContainer.appendChild(fileSection);
                }});
            }}

            // Store current program for prompt switching
            window.explorerCurrentProgram = program;

            // Render prompt function for explorer
            function renderExplorerPrompt(promptType) {{
                const prog = window.explorerCurrentProgram;
                if (!prog) return;

                let promptText = '';
                let emptyMessage = '';

                if (promptType === 'analyzer') {{
                    promptText = prog.analysis_prompt_used || '';
                    emptyMessage = 'No analyzer prompt available (initial program or analyzer disabled)';
                }} else {{
                    promptText = prog.mutator_prompt_used || '';
                    emptyMessage = 'No mutator prompt available (initial program)';
                }}

                let promptHtml = '';
                if (promptText && promptText.trim()) {{
                    if (typeof marked !== 'undefined') {{
                        promptHtml = `<div class="prompt-content markdown-body">${{marked.parse(promptText)}}</div>`;
                    }} else {{
                        promptHtml = `<pre style="white-space: pre-wrap; background: #0d1117; padding: 15px; border-radius: 6px;">${{escapeHtml(promptText)}}</pre>`;
                    }}
                }} else {{
                    promptHtml = `<p class="empty-state">${{emptyMessage}}</p>`;
                }}

                document.getElementById('explorer-prompt-view').innerHTML = promptHtml;

                // Apply syntax highlighting
                document.querySelectorAll('#explorer-prompt-view pre code').forEach(block => {{
                    if (typeof hljs !== 'undefined') {{
                        hljs.highlightElement(block);
                    }}
                }});
            }}

            // Initial prompt render
            renderExplorerPrompt('analyzer');

            // Setup prompt sub-tab switching
            panel.querySelectorAll('.prompt-subtab').forEach(btn => {{
                btn.onclick = function() {{
                    panel.querySelectorAll('.prompt-subtab').forEach(b => b.classList.remove('active'));
                    this.classList.add('active');
                    renderExplorerPrompt(this.dataset.explorerPrompt);
                }};
            }});

            // Setup main tab switching
            panel.querySelectorAll('.tabs .tab').forEach(tab => {{
                tab.addEventListener('click', () => {{
                    const tabId = tab.dataset.explorerTab;
                    panel.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
                    panel.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    tab.classList.add('active');
                    document.getElementById(`explorer-tab-${{tabId}}`).classList.add('active');
                }});
            }});
        }}

        // Update explorer tree highlighting without re-rendering (preserves scroll position)
        function updateExplorerHighlight(programId) {{
            const prevSelectedId = explorerSelectedId;
            explorerSelectedId = programId;

            const container = d3.select('#explorer-tree-container');
            const currentBestId = findBestProgramId();

            // Helper to check if node evaluation failed
            function isFailed(d) {{
                return d.data.has_error || !d.data.metrics;
            }}

            // Clear all path highlighting
            container.selectAll('.node').classed('on-path', false);
            container.selectAll('.link').classed('on-path', false);

            // Reset previous selected node's style
            if (prevSelectedId) {{
                container.selectAll('.node').each(function(d) {{
                    if (d.data.id === prevSelectedId) {{
                        const circle = d3.select(this).select('circle');
                        const isBest = d.data.id === currentBestId;
                        circle
                            .attr('r', isBest ? 10 : 7)
                            .attr('stroke', () => {{
                                if (isBest) return '#ffd700';
                                if (isFailed(d)) return '#f85149';
                                const color = safeColor(getScore(d.data));
                                const darkerColor = d3.color(color);
                                return darkerColor ? darkerColor.darker() : color;
                            }})
                            .attr('stroke-width', isFailed(d) ? 2 : 1.5);
                    }}
                }});
            }}

            // Set new selected node's style
            container.selectAll('.node').each(function(d) {{
                if (d.data.id === programId) {{
                    d3.select(this).select('circle')
                        .attr('r', 10)
                        .attr('stroke', '#58a6ff')
                        .attr('stroke-width', 3);
                }}
            }});

            // Highlight path to selected node
            if (explorerTreeRoot) {{
                const selectedNode = findNodeById(explorerTreeRoot, programId);
                if (selectedNode) {{
                    const ancestors = selectedNode.ancestors();
                    const ancestorIds = new Set(ancestors.map(n => n.data.id));

                    container.selectAll('.node').each(function(d) {{
                        if (d && d.data && ancestorIds.has(d.data.id)) {{
                            d3.select(this).classed('on-path', true);
                        }}
                    }});

                    container.selectAll('.link').each(function(d) {{
                        if (d && d.source && d.target) {{
                            const sourceOnPath = ancestorIds.has(d.source.data.id);
                            const targetOnPath = ancestorIds.has(d.target.data.id);
                            if (sourceOnPath && targetOnPath) {{
                                d3.select(this).classed('on-path', true);
                            }}
                        }}
                    }});
                }}
            }}
        }}

        // Navigate to a node in the explorer (from ancestry path click)
        function explorerNavigateTo(programId) {{
            showExplorerDetail(programId);
            updateExplorerHighlight(programId);
        }}

        // Setup resizable panels in explorer
        function setupExplorerResizer() {{
            const resizer = document.getElementById('explorer-resizer');
            const leftPanel = document.querySelector('.explorer-left');
            const rightPanel = document.querySelector('.explorer-right');
            const modalBody = document.querySelector('#tree-explorer-modal .modal-body');

            if (!resizer || !leftPanel || !rightPanel || !modalBody) return;

            let isResizing = false;

            resizer.addEventListener('mousedown', (e) => {{
                isResizing = true;
                resizer.classList.add('dragging');
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';
                e.preventDefault();
            }});

            document.addEventListener('mousemove', (e) => {{
                if (!isResizing) return;

                const containerRect = modalBody.getBoundingClientRect();
                const newLeftWidth = e.clientX - containerRect.left;
                const containerWidth = containerRect.width;

                // Limit min/max width (300px minimum on each side)
                const minWidth = 300;
                const resizerWidth = 6;
                const maxLeftWidth = containerWidth - minWidth - resizerWidth;

                if (newLeftWidth >= minWidth && newLeftWidth <= maxLeftWidth) {{
                    leftPanel.style.width = newLeftWidth + 'px';
                    leftPanel.style.flex = 'none';
                    rightPanel.style.width = (containerWidth - newLeftWidth - resizerWidth) + 'px';
                    rightPanel.style.flex = 'none';
                }}
            }});

            document.addEventListener('mouseup', () => {{
                if (isResizing) {{
                    isResizing = false;
                    resizer.classList.remove('dragging');
                    document.body.style.cursor = '';
                    document.body.style.userSelect = '';
                }}
            }});
        }}

        // Render path analysis chart for explorer
        function renderExplorerPathChart(ancestors) {{
            const container = document.getElementById('explorer-path-chart-container');
            container.innerHTML = '';

            if (!ancestors || ancestors.length < 2) {{
                container.innerHTML = '<p class="empty-state" style="margin: 0; padding: 20px;">Path too short for analysis</p>';
                return;
            }}

            // Extract path data from ancestors
            const pathData = ancestors.map((n, i) => {{
                const prog = programsData[n.data.id];
                return {{
                    step: i,
                    id: n.data.id,
                    label: n.data.order >= 0 ? `#${{n.data.order}}` : n.data.name,
                    metrics: prog ? prog.metrics : {{}},
                    mutation_summary: prog ? prog.mutation_summary : '',
                    fitness_delta: prog ? prog.fitness_delta : null
                }};
            }});

            // Get metrics to display (only *_score metrics)
            const scoreMetrics = Object.keys(pathData[0].metrics || {{}})
                .filter(k => k.endsWith('_score') && !k.startsWith('best_'));

            if (scoreMetrics.length === 0) {{
                container.innerHTML = '<p class="empty-state" style="margin: 0; padding: 20px;">No metrics available</p>';
                return;
            }}

            // Track visible metrics for this chart (default to function_score only)
            const pathVisibleMetrics = new Set(scoreMetrics.includes('fitness_score') ? ['fitness_score'] : (scoreMetrics.includes('function_score') ? ['function_score'] : scoreMetrics.slice(0, 1)));

            // Chart dimensions
            const width = container.clientWidth || 400;
            const height = 160;
            const margin = {{top: 15, right: 15, bottom: 35, left: 45}};

            const svg = d3.select('#explorer-path-chart-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            // X scale (steps along path)
            const x = d3.scaleLinear()
                .domain([0, pathData.length - 1])
                .range([margin.left, width - margin.right]);

            // Y scale (will be updated dynamically)
            const y = d3.scaleLinear()
                .range([height - margin.bottom, margin.top]);

            // Compute Y domain based on visible metrics
            function computeYDomain() {{
                const values = pathData.flatMap(d =>
                    Array.from(pathVisibleMetrics).map(m => d.metrics[m]).filter(v => typeof v === 'number')
                );
                if (values.length === 0) return [0, 1];
                const yMin = Math.min(...values);
                const yMax = Math.max(...values);
                const yPadding = (yMax - yMin) * 0.1 || 0.1;
                return [yMin - yPadding, yMax + yPadding];
            }}

            // Grid and axis groups
            const gridGroup = svg.append('g').attr('class', 'grid');
            const yAxisGroup = svg.append('g')
                .attr('class', 'y-axis')
                .attr('transform', `translate(${{margin.left}},0)`)
                .attr('color', '#8b949e');
            const linesGroup = svg.append('g').attr('class', 'lines');
            const pointsGroup = svg.append('g').attr('class', 'points');

            // X axis (static)
            svg.append('g')
                .attr('transform', `translate(0,${{height - margin.bottom}})`)
                .call(d3.axisBottom(x)
                    .ticks(Math.min(pathData.length, 10))
                    .tickFormat(i => {{
                        const d = pathData[Math.round(i)];
                        return d ? d.label : '';
                    }}))
                .attr('color', '#8b949e')
                .selectAll('text')
                .attr('font-size', '10px');

            // Hover interaction elements
            const focus = svg.append('g').style('display', 'none');

            // Vertical indicator line
            focus.append('line')
                .attr('class', 'hover-line')
                .attr('y1', margin.top)
                .attr('y2', height - margin.bottom)
                .attr('stroke', '#58a6ff')
                .attr('stroke-width', 1)
                .attr('stroke-dasharray', '4,4');

            // Draw function (called on initial render and when toggling metrics)
            function drawPathLines() {{
                // Update Y domain
                y.domain(computeYDomain());

                // Update grid
                gridGroup.selectAll('*').remove();
                gridGroup.attr('stroke', '#30363d').attr('stroke-opacity', 0.5)
                    .selectAll('line')
                    .data(y.ticks(4))
                    .join('line')
                    .attr('x1', margin.left)
                    .attr('x2', width - margin.right)
                    .attr('y1', d => y(d))
                    .attr('y2', d => y(d));

                // Update Y axis
                yAxisGroup.call(d3.axisLeft(y).ticks(4));

                // Clear and redraw lines
                linesGroup.selectAll('*').remove();
                pointsGroup.selectAll('*').remove();

                scoreMetrics.forEach((metric, idx) => {{
                    if (!pathVisibleMetrics.has(metric)) return;

                    const color = getMetricColor(metric, idx);
                    const validData = pathData.filter(d => typeof d.metrics[metric] === 'number');
                    if (validData.length < 2) return;

                    const line = d3.line()
                        .x(d => x(d.step))
                        .y(d => y(d.metrics[metric]));

                    // Visible line
                    linesGroup.append('path')
                        .datum(validData)
                        .attr('fill', 'none')
                        .attr('stroke', color)
                        .attr('stroke-width', 1.5)
                        .attr('d', line);

                    // Points (clickable)
                    pointsGroup.selectAll(`.explorer-path-point-${{idx}}`)
                        .data(validData)
                        .join('circle')
                        .attr('class', `explorer-path-point-${{idx}}`)
                        .attr('cx', d => x(d.step))
                        .attr('cy', d => y(d.metrics[metric]))
                        .attr('r', 4)
                        .attr('fill', color)
                        .attr('stroke', '#0d1117')
                        .attr('stroke-width', 1)
                        .style('cursor', 'pointer')
                        .on('click', (event, d) => explorerNavigateTo(d.id));
                }});
            }}

            // Transparent overlay to capture mouse events
            svg.append('rect')
                .attr('class', 'explorer-path-chart-overlay')
                .attr('x', margin.left)
                .attr('y', margin.top)
                .attr('width', width - margin.left - margin.right)
                .attr('height', height - margin.top - margin.bottom)
                .style('fill', 'none')
                .style('pointer-events', 'all')
                .style('cursor', 'pointer')
                .on('mouseover', () => focus.style('display', null))
                .on('mouseout', () => {{
                    focus.style('display', 'none');
                    hideTooltip();
                }})
                .on('mousemove', function(event) {{
                    const mouseX = d3.pointer(event)[0];
                    const targetStep = Math.round(x.invert(mouseX));
                    const clampedStep = Math.max(0, Math.min(pathData.length - 1, targetStep));
                    const dataPoint = pathData[clampedStep];

                    if (!dataPoint) return;

                    // Update vertical line position
                    focus.select('.hover-line').attr('x1', x(clampedStep)).attr('x2', x(clampedStep));

                    // Build tooltip content showing all visible metrics
                    const tooltip = document.getElementById('tooltip');
                    let html = `<h4>${{dataPoint.label}}</h4>`;
                    html += `<p style="color: #8b949e; font-size: 0.85em;">ID: ${{dataPoint.id ? dataPoint.id.substring(0, 8) : '-'}}</p>`;
                    html += '<hr style="border-color: #30363d; margin: 8px 0;">';

                    // Show mutation summary if available
                    if (dataPoint.mutation_summary) {{
                        const deltaStr = dataPoint.fitness_delta !== null
                            ? ` (${{dataPoint.fitness_delta >= 0 ? '+' : ''}}${{(dataPoint.fitness_delta * 100).toFixed(1)}}%)`
                            : '';
                        html += `<p style="color: #a5d6ff; font-style: italic; margin-bottom: 8px;">"${{dataPoint.mutation_summary}}"${{deltaStr}}</p>`;
                    }}

                    // Show values for all visible metrics
                    scoreMetrics.forEach((metric, idx) => {{
                        if (!pathVisibleMetrics.has(metric)) return;
                        const color = getMetricColor(metric, idx);
                        const value = dataPoint.metrics[metric];
                        if (typeof value === 'number') {{
                            html += `<p><span style="display:inline-block;width:10px;height:10px;background:${{color}};border-radius:2px;margin-right:6px;"></span><strong>${{metric.replace(/_/g, ' ')}}:</strong> ${{value.toFixed(4)}}</p>`;
                        }}
                    }});

                    tooltip.innerHTML = html;
                    tooltip.style.display = 'block';
                    tooltip.style.left = (event.pageX + 15) + 'px';
                    tooltip.style.top = (event.pageY - 10) + 'px';
                }})
                .on('click', function(event) {{
                    const mouseX = d3.pointer(event)[0];
                    const targetStep = Math.round(x.invert(mouseX));
                    const clampedStep = Math.max(0, Math.min(pathData.length - 1, targetStep));
                    const dataPoint = pathData[clampedStep];

                    if (dataPoint && dataPoint.id) {{
                        explorerNavigateTo(dataPoint.id);
                    }}
                }});

            // Initial draw
            drawPathLines();

            // Interactive legend
            const legendContainer = d3.select('#explorer-path-chart-container')
                .append('div')
                .style('display', 'flex')
                .style('flex-wrap', 'wrap')
                .style('justify-content', 'center')
                .style('gap', '8px')
                .style('margin-top', '8px');

            scoreMetrics.forEach((metric, idx) => {{
                const color = getMetricColor(metric, idx);

                const btn = legendContainer.append('div')
                    .style('display', 'flex')
                    .style('align-items', 'center')
                    .style('gap', '5px')
                    .style('padding', '4px 10px')
                    .style('background', pathVisibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                    .style('border', '1px solid ' + (pathVisibleMetrics.has(metric) ? color : '#30363d'))
                    .style('border-radius', '4px')
                    .style('cursor', 'pointer')
                    .style('font-size', '11px')
                    .style('color', pathVisibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e')
                    .on('click', function() {{
                        if (pathVisibleMetrics.has(metric)) {{
                            pathVisibleMetrics.delete(metric);
                        }} else {{
                            pathVisibleMetrics.add(metric);
                        }}
                        d3.select(this)
                            .style('background', pathVisibleMetrics.has(metric) ? '#21262d' : '#0d1117')
                            .style('border-color', pathVisibleMetrics.has(metric) ? color : '#30363d')
                            .style('color', pathVisibleMetrics.has(metric) ? '#c9d1d9' : '#8b949e');
                        drawPathLines();
                    }});

                btn.append('div')
                    .style('width', '12px')
                    .style('height', '3px')
                    .style('background', color);

                btn.append('span').text(metric.replace(/_/g, ' '));
            }});
        }}

        // Tab switching
        function setupTabs() {{
            document.querySelectorAll('.tab').forEach(tab => {{
                tab.addEventListener('click', () => {{
                    const tabId = tab.dataset.tab;

                    // Update tab buttons
                    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active');

                    // Update content
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    document.getElementById(`tab-${{tabId}}`).classList.add('active');
                }});
            }});
        }}

        // Island colors for multi-island visualization
        const islandColors = ['#58a6ff', '#f78166', '#a371f7', '#3fb950', '#d29922', '#ff7b72'];

        // Current heatmap state
        let currentXDim = null;
        let currentYDim = null;
        let currentIsland = 'all';

        // Initialize heatmap controls
        function initHeatmapControls() {{
            const dims = summaryStats.feature_dimensions || [];
            const numIslands = summaryStats.num_islands || 1;

            // Populate island selector
            const islandSelect = document.getElementById('island-select');
            for (let i = 0; i < numIslands; i++) {{
                const opt = document.createElement('option');
                opt.value = i;
                opt.textContent = `Island ${{i}}`;
                islandSelect.appendChild(opt);
            }}

            // Populate dimension selectors
            const xDimSelect = document.getElementById('x-dim-select');
            const yDimSelect = document.getElementById('y-dim-select');

            dims.forEach((dim, idx) => {{
                const optX = document.createElement('option');
                optX.value = dim;
                optX.textContent = dim;
                if (idx === 0) optX.selected = true;
                xDimSelect.appendChild(optX);

                const optY = document.createElement('option');
                optY.value = dim;
                optY.textContent = dim;
                if (idx === 1) optY.selected = true;
                yDimSelect.appendChild(optY);
            }});

            // Set initial values
            currentXDim = dims[0] || null;
            currentYDim = dims[1] || dims[0] || null;

            // Add event listeners
            islandSelect.addEventListener('change', (e) => {{
                currentIsland = e.target.value;
                renderHeatmapContent();
            }});
            xDimSelect.addEventListener('change', (e) => {{
                currentXDim = e.target.value;
                renderHeatmapContent();
            }});
            yDimSelect.addEventListener('change', (e) => {{
                currentYDim = e.target.value;
                renderHeatmapContent();
            }});

            // Render island legend
            renderIslandLegend(numIslands);
        }}

        // Render island legend
        function renderIslandLegend(numIslands) {{
            const container = document.getElementById('island-legend');
            container.innerHTML = '';  // Clear existing legend
            if (numIslands <= 1) return;

            for (let i = 0; i < numIslands; i++) {{
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; align-items: center; gap: 4px;';
                item.innerHTML = `
                    <div style="width: 12px; height: 12px; border: 2px solid ${{islandColors[i % islandColors.length]}}; border-radius: 2px;"></div>
                    <span style="color: #8b949e; font-size: 0.85em;">Island ${{i}}</span>
                `;
                container.appendChild(item);
            }}
        }}

        // Render MAP-Elites heatmap
        function renderHeatmap() {{
            initHeatmapControls();
            renderHeatmapContent();
        }}

        // Render heatmap content (called on filter change)
        function renderHeatmapContent() {{
            const container = document.getElementById('heatmap-container');
            container.innerHTML = '';  // Clear previous content

            const width = container.clientWidth;
            const height = 350;

            const svg = d3.select('#heatmap-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            if (mapElitesData.length === 0 || !currentXDim) {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No MAP-Elites data available');
                return;
            }}

            // Filter data by island
            let filteredData = mapElitesData;
            if (currentIsland !== 'all') {{
                filteredData = mapElitesData.filter(d => d.island_id === parseInt(currentIsland));
            }}

            // Transform data to use selected dimensions
            const transformedData = filteredData.map(d => ({{
                ...d,
                x: d.coords[currentXDim] || 0,
                y: d.coords[currentYDim] || 0
            }}));

            if (transformedData.length === 0) {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No data for selected filters');
                return;
            }}

            const margin = {{top: 40, right: 40, bottom: 60, left: 60}};
            const gridWidth = width - margin.left - margin.right;
            const gridHeight = height - margin.top - margin.bottom;

            // Determine grid dimensions
            const numBins = summaryStats.config.feature_bins || 10;
            const cellWidth = gridWidth / numBins;
            const cellHeight = gridHeight / numBins;

            const g = svg.append('g')
                .attr('transform', `translate(${{margin.left}},${{margin.top}})`);

            // Draw grid background
            for (let i = 0; i < numBins; i++) {{
                for (let j = 0; j < numBins; j++) {{
                    g.append('rect')
                        .attr('x', i * cellWidth)
                        .attr('y', j * cellHeight)
                        .attr('width', cellWidth - 1)
                        .attr('height', cellHeight - 1)
                        .attr('fill', '#21262d')
                        .attr('rx', 2);
                }}
            }}

            // Draw cells
            g.selectAll('.heatmap-cell')
                .data(transformedData)
                .join('rect')
                .attr('class', 'heatmap-cell')
                .attr('x', d => d.x * cellWidth + 1)
                .attr('y', d => d.y * cellHeight + 1)
                .attr('width', cellWidth - 3)
                .attr('height', cellHeight - 3)
                .attr('fill', d => safeColor(getScore(d)))
                .attr('stroke', d => currentIsland === 'all' ? islandColors[d.island_id % islandColors.length] : 'none')
                .attr('stroke-width', currentIsland === 'all' ? 2 : 0)
                .attr('rx', 3)
                .style('cursor', 'pointer')
                .on('click', (event, d) => showProgramDetail(d.program_id))
                .on('mouseover', (event, d) => {{
                    const tooltip = document.getElementById('tooltip');
                    // Build metrics HTML
                    let metricsHtml = '';
                    if (d.metrics && Object.keys(d.metrics).length > 0) {{
                        metricsHtml = '<hr style="border-color: #30363d; margin: 8px 0;">';
                        for (const [key, value] of Object.entries(d.metrics)) {{
                            const displayValue = typeof value === 'number' ? value.toFixed(4) : value;
                            const isSelected = key === selectedMetric;
                            const style = isSelected ? 'color: #58a6ff;' : '';
                            metricsHtml += `<p style="${{style}}"><strong>${{key}}:</strong> ${{displayValue}}</p>`;
                        }}
                    }}
                    // Build coordinates HTML
                    let coordsHtml = '';
                    if (d.coords) {{
                        coordsHtml = '<hr style="border-color: #30363d; margin: 8px 0;"><p style="color: #8b949e;">Bin coordinates:</p>';
                        for (const [dim, bin] of Object.entries(d.coords)) {{
                            const isAxis = dim === currentXDim || dim === currentYDim;
                            const style = isAxis ? 'color: #58a6ff;' : '';
                            coordsHtml += `<p style="${{style}}"><strong>${{dim}}:</strong> bin ${{bin}}</p>`;
                        }}
                    }}
                    const isBest = d.program_id === findBestProgramId();
                    const progData = programsData[d.program_id] || {{}};
                    const orderStr = progData.order !== undefined && progData.order >= 0 ? '#' + progData.order : '#' + d.generation;
                    tooltip.innerHTML = `
                        <h4>Program ${{orderStr}} <span style="color: #8b949e; font-size: 0.8em;">(${{d.program_id.substring(0, 8)}})</span></h4>
                        ${{progData.order !== undefined && progData.order >= 0 ? '<p><strong>Order:</strong> ' + progData.order + '</p>' : ''}}
                        <p><strong>Island:</strong> ${{d.island_id}}</p>
                        <p><strong>Generation:</strong> ${{d.generation}}</p>
                        ${{isBest ? '<p style="color: #ffd700;"><strong>★ Best for ' + selectedMetric.replace(/_/g, ' ') + '</strong></p>' : ''}}
                        ${{metricsHtml}}
                        ${{coordsHtml}}
                    `;
                    tooltip.style.display = 'block';
                    tooltip.style.left = (event.pageX + 10) + 'px';
                    tooltip.style.top = (event.pageY + 10) + 'px';
                }})
                .on('mouseout', hideTooltip);

            // Axes labels with adaptive ranges
            const ranges = summaryStats.feature_ranges || {{}};

            function formatAxisLabel(dim, rangeData) {{
                if (rangeData && rangeData[dim]) {{
                    const [minVal, maxVal] = rangeData[dim];
                    return `${{dim}} (${{minVal.toFixed(2)}} - ${{maxVal.toFixed(2)}})`;
                }}
                return dim || 'Feature';
            }}

            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height - 10)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text(formatAxisLabel(currentXDim, ranges));

            svg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', 15)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text(formatAxisLabel(currentYDim, ranges));

            // Draw axis ticks
            const xTicks = g.append('g').attr('transform', `translate(0,${{gridHeight}})`);
            const yTicks = g.append('g');

            for (let i = 0; i <= numBins; i += 2) {{
                xTicks.append('text')
                    .attr('x', i * cellWidth)
                    .attr('y', 15)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .attr('font-size', '10px')
                    .text(i);

                yTicks.append('text')
                    .attr('x', -8)
                    .attr('y', i * cellHeight + 4)
                    .attr('text-anchor', 'end')
                    .attr('fill', '#8b949e')
                    .attr('font-size', '10px')
                    .text(i);
            }}
        }}
        // Render configuration
        function renderConfig() {{
            const config = summaryStats.config || {{}};
            const container = document.getElementById('config-display');
            if (Object.keys(config).length === 0) {{
                container.innerHTML = '<p style="color: #8b949e; padding: 15px;">No configuration available</p>';
                return;
            }}
            let html = '<table class="config-table"><tbody>';
            for (const [key, value] of Object.entries(config)) {{
                let displayValue;
                if (Array.isArray(value)) {{
                    displayValue = value.join(', ');
                }} else if (typeof value === 'object' && value !== null) {{
                    displayValue = JSON.stringify(value);
                }} else if (value === null || value === undefined) {{
                    displayValue = '-';
                }} else {{
                    displayValue = String(value);
                }}
                html += `<tr><td class="config-key">${{key}}</td><td class="config-value">${{displayValue}}</td></tr>`;
            }}
            html += '</tbody></table>';
            container.innerHTML = html;
        }}
    </script>

    <!-- Objective and Configuration -->
    <div class="container">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px;">
            <!-- Optimization Objective -->
            <div id="objective-section" style="display: none; background: #161b22; border: 1px solid #30363d; border-radius: 8px;">
                <h2 style="padding: 15px 20px; background: #21262d; border-bottom: 1px solid #30363d; margin: 0; font-size: 1.2em; color: #c9d1d9;">Optimization Objective</h2>
                <div id="objective-content" style="padding: 15px 20px; white-space: pre-wrap; line-height: 1.6; color: #c9d1d9; font-size: 0.9em; max-height: 400px; overflow-y: auto;"></div>
            </div>
            <!-- Configuration -->
            <div style="background: #161b22; border: 1px solid #30363d; border-radius: 8px;">
                <h2 style="padding: 15px 20px; background: #21262d; border-bottom: 1px solid #30363d; margin: 0; font-size: 1.2em; color: #c9d1d9;">Evolution Configuration</h2>
                <div id="config-display" style="max-height: 400px; overflow-y: auto;"></div>
            </div>
        </div>
    </div>

    <script>renderConfig();</script>

    <!-- Tree Explorer Modal -->
    <div id="tree-explorer-modal" class="modal">
        <div class="modal-content tree-explorer">
            <div class="modal-header">
                <h2>Evolution Tree Explorer</h2>
                <button class="modal-close-btn" onclick="closeTreeExplorer()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="explorer-left">
                    <div id="explorer-tree-container"></div>
                </div>
                <div class="explorer-resizer" id="explorer-resizer"></div>
                <div class="explorer-right">
                    <div id="explorer-detail-panel">
                        <p class="explorer-empty-state">Click a node to view details</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <footer>
        Generated by <a href="https://pantheonos.stanford.edu/" target="_blank">Pantheon</a>
    </footer>
</body>
</html>'''


def generate_evolution_report(db_path: str, output_path: str) -> str:
    """
    Convenience function to generate HTML report.

    Args:
        db_path: Path to evolution_results directory
        output_path: Path to save HTML file

    Returns:
        Path to generated HTML file
    """
    visualizer = EvolutionVisualizer.from_path(db_path)
    return visualizer.generate_html(output_path)

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

    def __init__(self, database: EvolutionDatabase):
        """
        Initialize visualizer with a loaded database.

        Args:
            database: Loaded EvolutionDatabase
        """
        self.database = database
        self.programs = database.programs
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
        return cls(database)

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
        # Compute iteration numbers based on creation order
        sorted_programs = sorted(
            self.programs.values(),
            key=lambda p: p.created_at
        )
        iteration_map = {p.id: i for i, p in enumerate(sorted_programs)}

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
            score = prog.metrics.get("combined_score", 0.0)

            node = {
                "id": prog_id,
                "name": prog_id[:8],
                "iteration": iteration_map.get(prog_id, 0),
                "generation": prog.generation,
                "island_id": prog.island_id,
                "score": score,
                "metrics": prog.metrics,
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
        Get score history sorted by creation time.

        Returns:
            List of {iteration, program_id, <metric_name>: value, best_<metric_name>: value, ...}
        """
        # Collect all metric keys from all programs
        all_metric_keys = set()
        for prog in self.programs.values():
            all_metric_keys.update(prog.metrics.keys())

        # Sort programs by creation time
        sorted_programs = sorted(
            self.programs.values(),
            key=lambda p: p.created_at
        )

        history = []
        best_scores: Dict[str, float] = {}  # Track best value for each metric

        for i, prog in enumerate(sorted_programs):
            entry = {
                "iteration": i,
                "program_id": prog.id,
            }

            # Add all metrics and their best values
            for key in all_metric_keys:
                value = prog.metrics.get(key, 0.0)
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
        return sorted(all_metric_keys)

    def get_programs_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all programs data for detail view.

        Returns:
            Dict mapping program_id to program details
        """
        programs_data = {}

        for prog_id, prog in self.programs.items():
            programs_data[prog_id] = {
                "id": prog_id,
                "parent_id": prog.parent_id,
                "generation": prog.generation,
                "island_id": prog.island_id,
                "metrics": prog.metrics,
                "diff": prog.diff_from_parent or "",
                "llm_feedback": prog.llm_feedback or prog.artifacts.get("llm_feedback", ""),
                "issues": prog.artifacts.get("issues", []),
                "suggestions": prog.artifacts.get("suggestions", []),
                "created_at": prog.created_at,
                "is_best": prog_id == self.database.best_program_id,
                "code_preview": self._get_code_preview(prog),
            }

        return programs_data

    def _get_code_preview(self, prog: Program, max_lines: int = 50) -> str:
        """Get a preview of the program code."""
        code = prog.get_combined_code()
        lines = code.split("\n")
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines)"
        return code

    def get_map_elites_data(self) -> List[Dict[str, Any]]:
        """
        Get MAP-Elites grid data for heatmap visualization.

        Returns:
            List of {coords, score, program_id, metrics, ...} for each filled cell
        """
        cells = []
        feature_dims = self.metadata.get("config", {}).get("feature_dimensions", [])

        for island_id, feature_map in enumerate(self.database.island_feature_maps):
            for coords, prog_id in feature_map.items():
                if prog_id in self.programs:
                    prog = self.programs[prog_id]
                    score = prog.metrics.get("combined_score", 0.0)

                    # Store full coordinates as dict mapping dimension name to bin index
                    coords_dict = {}
                    for i, dim in enumerate(feature_dims):
                        if i < len(coords):
                            coords_dict[dim] = coords[i]

                    cells.append({
                        "coords": coords_dict,  # Full coordinates by dimension name
                        "score": score,
                        "program_id": prog_id,
                        "island_id": island_id,
                        "metrics": prog.metrics,
                        "generation": prog.generation,
                    })

        return cells

    def get_summary_stats(self) -> Dict[str, Any]:
        """Get summary statistics for the evolution run."""
        stats = self.database.get_statistics()

        # Calculate additional stats
        scores = [p.metrics.get("combined_score", 0.0) for p in self.programs.values()]

        best_prog = self.database.get_best_program()
        initial_score = 0.0

        # Find initial program (generation 0)
        for prog in self.programs.values():
            if prog.generation == 0:
                initial_score = prog.metrics.get("combined_score", 0.0)
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

        # Convert data to JSON for embedding
        tree_json = json.dumps(tree_data, ensure_ascii=False)
        history_json = json.dumps(score_history, ensure_ascii=False)
        programs_json = json.dumps(programs_data, ensure_ascii=False)
        map_elites_json = json.dumps(map_elites_data, ensure_ascii=False)
        stats_json = json.dumps(summary_stats, ensure_ascii=False)
        metric_keys_json = json.dumps(metric_keys, ensure_ascii=False)

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
            z-index: 1000;
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

        .diff-container h4 {{
            background: #21262d;
            padding: 10px 15px;
            margin: 0;
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
        }}

        .tab-content.active {{
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
            <span style="color: #8b949e; font-size: 0.85em; margin-left: 10px;">
                (Changes tree node colors and MAP-Elites heatmap)
            </span>
        </div>

        <!-- Evolution Tree -->
        <section>
            <h2>Evolution Tree</h2>
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

                <div class="metrics-grid" id="detail-metrics"></div>

                <div class="tabs">
                    <div class="tab active" data-tab="diff">Diff</div>
                    <div class="tab" data-tab="feedback">LLM Feedback</div>
                    <div class="tab" data-tab="code">Code Preview</div>
                </div>

                <div class="tab-content active" id="tab-diff">
                    <div class="diff-container" id="diff-view"></div>
                </div>

                <div class="tab-content" id="tab-feedback">
                    <div class="feedback-section" id="feedback-view"></div>
                </div>

                <div class="tab-content" id="tab-code">
                    <pre style="background: #0d1117; padding: 15px; border-radius: 6px; overflow-x: auto;" id="code-view"></pre>
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

        // Color palette for metrics
        const metricColors = {{
            'combined_score': '#58a6ff',
            'mixing_score': '#a371f7',
            'bio_conservation_score': '#3fb950',
            'speed_score': '#f0883e',
            'convergence_score': '#f778ba',
            'execution_time': '#79c0ff',
            'iterations': '#ffa657',
        }};

        // Default color for unknown metrics
        const defaultColors = ['#58a6ff', '#a371f7', '#3fb950', '#f0883e', '#f778ba', '#79c0ff', '#ffa657', '#ff7b72'];

        function getMetricColor(metric, index) {{
            return metricColors[metric] || defaultColors[index % defaultColors.length];
        }}

        // Track which metrics are visible
        const visibleMetrics = new Set(['combined_score', 'best_combined_score']);

        // Currently selected metric for coloring
        let selectedMetric = 'combined_score';

        // Store tree root for ancestry path lookup
        let treeRoot = null;

        // Color scale for scores (will be updated based on selected metric)
        const colorScale = d3.scaleLinear()
            .domain([0, 0.5, 1])
            .range(['#f85149', '#d29922', '#3fb950']);

        // Compute color scale domain based on selected metric
        function updateColorScale() {{
            // Collect all values for the selected metric
            const values = [];
            for (const prog of Object.values(programsData)) {{
                if (prog.metrics && prog.metrics[selectedMetric] !== undefined) {{
                    values.push(prog.metrics[selectedMetric]);
                }}
            }}
            if (values.length > 0) {{
                const minVal = Math.min(...values);
                const maxVal = Math.max(...values);
                const midVal = (minVal + maxVal) / 2;
                colorScale.domain([minVal, midVal, maxVal]);
            }}
        }}

        // Get score for a program based on selected metric
        function getScore(data) {{
            if (data.metrics && data.metrics[selectedMetric] !== undefined) {{
                return data.metrics[selectedMetric];
            }}
            return data.score || 0;
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
                if (metric === 'combined_score') {{
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

        // Get filtered score history based on checkbox state
        function getFilteredScoreHistory() {{
            const hideFailed = document.getElementById('hide-failed').checked;
            if (!hideFailed) return scoreHistory;
            return scoreHistory.filter(d => d.function_score && d.function_score > 0);
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

            // Build list of all metrics (only *_score metrics, exclude best_* variants for legend)
            const allMetrics = metricKeys.filter(k => !k.startsWith('best_') && k.endsWith('_score'));
            const bestMetrics = metricKeys.filter(k => k.startsWith('best_') && k.endsWith('_score'));

            // Function to compute y-domain based on visible metrics only
            function computeYDomain() {{
                let minVal = Infinity, maxVal = -Infinity;
                chartData.forEach(d => {{
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

            const x = d3.scaleLinear()
                .domain([0, chartData.length - 1])
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

                    // Current value line
                    if (visibleMetrics.has(metric)) {{
                        const line = d3.line()
                            .defined(d => d[metric] !== undefined)
                            .x((d, i) => x(i))
                            .y(d => y(d[metric] || 0));

                        linesGroup.append('path')
                            .datum(chartData)
                            .attr('fill', 'none')
                            .attr('stroke', color)
                            .attr('stroke-width', 1.5)
                            .attr('stroke-opacity', 0.8)
                            .attr('d', line);
                    }}

                    // Best value line (dashed, thicker)
                    const bestKey = 'best_' + metric;
                    if (visibleMetrics.has(bestKey)) {{
                        const bestLine = d3.line()
                            .defined(d => d[bestKey] !== undefined)
                            .x((d, i) => x(i))
                            .y(d => y(d[bestKey] || 0));

                        linesGroup.append('path')
                            .datum(chartData)
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

            // Transparent overlay to capture mouse events
            svg.append('rect')
                .attr('class', 'chart-overlay')
                .attr('x', margin.left)
                .attr('y', margin.top)
                .attr('width', width - margin.left - margin.right)
                .attr('height', height - margin.top - margin.bottom)
                .style('fill', 'none')
                .style('pointer-events', 'all')
                .on('mouseover', () => focus.style('display', null))
                .on('mouseout', () => {{
                    focus.style('display', 'none');
                    hideTooltip();
                }})
                .on('mousemove', function(event) {{
                    // Find the nearest data point
                    const mouseX = d3.pointer(event)[0];
                    const x0 = x.invert(mouseX);
                    const i = Math.round(x0);
                    if (i < 0 || i >= chartData.length) return;

                    const dataPoint = chartData[i];

                    // Update vertical line position
                    focus.select('.hover-line').attr('x1', x(i)).attr('x2', x(i));

                    // Build tooltip content showing all visible metrics
                    const tooltip = document.getElementById('tooltip');
                    let html = `<h4>Iteration ${{i}}</h4>`;
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
                return !d.data.metrics || !d.data.metrics.function_score;
            }}

            // Circles - bind click events directly to circles
            nodes.append('circle')
                .attr('r', d => d.data.id === currentBestId ? 10 : 7)
                .attr('fill', d => {{
                    if (isFailed(d)) return '#6e7681';  // Gray for failed
                    return colorScale(getScore(d.data));
                }})
                .attr('stroke', d => {{
                    if (d.data.id === currentBestId) return '#ffd700';  // Gold for best
                    if (isFailed(d)) return '#f85149';  // Red for failed
                    return d3.color(colorScale(getScore(d.data))).darker();
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
            const evalFailed = !data.metrics || !data.metrics.function_score;
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
            tooltip.innerHTML = `
                <h4>Program: ${{data.name}}</h4>
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

                const panel = document.getElementById('detail-panel');
                panel.classList.add('active');
                console.log('Panel activated');

                document.getElementById('detail-title').textContent =
                    `Program: ${{programId.substring(0, 8)}}${{program.is_best ? ' (Best)' : ''}}`;

                // Build ancestry path
                const pathContainer = document.getElementById('ancestry-path');
                // Clear previous path highlighting
                d3.selectAll('.node').classed('on-path', false);
                d3.selectAll('.link').classed('on-path', false);

                if (treeRoot) {{
                    const node = findNodeById(treeRoot, programId);
                    if (node) {{
                        const ancestors = node.ancestors().reverse();  // Root to current
                        pathContainer.innerHTML = ancestors.map((n, i) => {{
                            const label = n.data.iteration >= 0 ? `#${{n.data.iteration}}` : n.data.name;
                            const isLast = i === ancestors.length - 1;
                            const nodeHtml = `<span class="path-node${{isLast ? ' current' : ''}}" onclick="showProgramDetail('${{n.data.id}}')">${{label}}</span>`;
                            return isLast ? nodeHtml : nodeHtml + '<span class="path-separator">→</span>';
                        }}).join('');

                        // Highlight nodes and links on path in the tree
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
                    }} else {{
                        pathContainer.innerHTML = '';
                    }}
                }} else {{
                    pathContainer.innerHTML = '';
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

                // Code preview
                document.getElementById('code-view').textContent = program.code_preview || 'No code preview available';

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

        // Helper function to escape HTML
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}

        function closeDetailPanel() {{
            document.getElementById('detail-panel').classList.remove('active');
            // Clear selection highlight
            d3.selectAll('.node circle').attr('stroke-width', 2);
            selectedNodeId = null;
            // Clear path highlighting
            d3.selectAll('.node').classed('on-path', false);
            d3.selectAll('.link').classed('on-path', false);
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
                .attr('fill', d => colorScale(getScore(d)))
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
                    tooltip.innerHTML = `
                        <h4>Program #${{d.generation}} <span style="color: #8b949e; font-size: 0.8em;">(${{d.program_id.substring(0, 8)}})</span></h4>
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

    <!-- Configuration -->
    <div class="container">
        <div style="background: #161b22; border: 1px solid #30363d; border-radius: 8px; margin-bottom: 30px;">
            <h2 style="padding: 15px 20px; background: #21262d; border-bottom: 1px solid #30363d; margin: 0; font-size: 1.2em; color: #c9d1d9;">Evolution Configuration</h2>
            <div id="config-display"></div>
        </div>
    </div>

    <script>renderConfig();</script>

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

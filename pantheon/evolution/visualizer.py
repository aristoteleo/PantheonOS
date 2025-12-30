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
            return {"id": "empty", "name": "Empty", "children": [], "score": 0}
        elif len(roots) == 1:
            return build_node(roots[0])
        else:
            # Multiple roots - create virtual parent
            return {
                "id": "root",
                "name": "Evolution",
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
            List of {iteration, score, best_score, program_id}
        """
        # Sort programs by creation time
        sorted_programs = sorted(
            self.programs.values(),
            key=lambda p: p.created_at
        )

        history = []
        best_score = 0.0

        for i, prog in enumerate(sorted_programs):
            score = prog.metrics.get("combined_score", 0.0)
            best_score = max(best_score, score)
            history.append({
                "iteration": i,
                "score": score,
                "best_score": best_score,
                "program_id": prog.id,
            })

        return history

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
            List of {x, y, score, program_id} for each filled cell
        """
        cells = []

        for island_id, feature_map in enumerate(self.database.island_feature_maps):
            for coords, prog_id in feature_map.items():
                if prog_id in self.programs:
                    prog = self.programs[prog_id]
                    score = prog.metrics.get("combined_score", 0.0)

                    # Handle different coordinate formats
                    if len(coords) >= 2:
                        x, y = coords[0], coords[1]
                    elif len(coords) == 1:
                        x, y = coords[0], 0
                    else:
                        continue

                    cells.append({
                        "x": x,
                        "y": y,
                        "score": score,
                        "program_id": prog_id,
                        "island_id": island_id,
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

        # Generate HTML
        html_content = self._render_html(
            tree_data=tree_data,
            score_history=score_history,
            programs_data=programs_data,
            map_elites_data=map_elites_data,
            summary_stats=summary_stats,
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
    ) -> str:
        """Render the complete HTML report."""

        # Convert data to JSON for embedding
        tree_json = json.dumps(tree_data, ensure_ascii=False)
        history_json = json.dumps(score_history, ensure_ascii=False)
        programs_json = json.dumps(programs_data, ensure_ascii=False)
        map_elites_json = json.dumps(map_elites_data, ensure_ascii=False)
        stats_json = json.dumps(summary_stats, ensure_ascii=False)

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Evolution Report - Pantheon</title>

    <!-- D3.js -->
    <script src="https://d3js.org/d3.v7.min.js"></script>

    <!-- diff2html -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/diff2html/bundles/css/diff2html.min.css">
    <script src="https://cdn.jsdelivr.net/npm/diff2html/bundles/js/diff2html-ui.min.js"></script>

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
            height: 300px;
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
            background-color: rgba(248, 81, 73, 0.15) !important;
        }}

        .d2h-ins {{
            background-color: rgba(63, 185, 80, 0.15) !important;
        }}

        .d2h-code-line-prefix {{
            color: #8b949e !important;
        }}

        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #8b949e;
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
            <p>Generated by Pantheon Evolution</p>
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
                <div id="chart-container"></div>
            </div>
        </section>

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
                <div id="heatmap-container"></div>
                <div class="legend">
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

        // Color scale for scores
        const colorScale = d3.scaleLinear()
            .domain([0, 0.5, 1])
            .range(['#f85149', '#d29922', '#3fb950']);

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {{
            renderStats();
            renderScoreChart();
            renderTree();
            renderHeatmap();
            setupTabs();
        }});

        // Render summary stats
        function renderStats() {{
            document.getElementById('stat-iterations').textContent = summaryStats.total_programs;
            document.getElementById('stat-best-score').textContent = summaryStats.best_score.toFixed(4);
            document.getElementById('stat-improvement').textContent =
                (summaryStats.improvement_pct >= 0 ? '+' : '') + summaryStats.improvement_pct.toFixed(1) + '%';
            document.getElementById('stat-islands').textContent = summaryStats.num_islands;
        }}

        // Render score history chart
        function renderScoreChart() {{
            const container = document.getElementById('chart-container');
            const width = container.clientWidth;
            const height = 280;
            const margin = {{top: 20, right: 30, bottom: 40, left: 60}};

            const svg = d3.select('#chart-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            if (scoreHistory.length === 0) {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No score history available');
                return;
            }}

            const x = d3.scaleLinear()
                .domain([0, scoreHistory.length - 1])
                .range([margin.left, width - margin.right]);

            const y = d3.scaleLinear()
                .domain([0, d3.max(scoreHistory, d => Math.max(d.score, d.best_score)) * 1.1])
                .range([height - margin.bottom, margin.top]);

            // Grid lines
            svg.append('g')
                .attr('stroke', '#30363d')
                .attr('stroke-opacity', 0.5)
                .call(g => g
                    .selectAll('line')
                    .data(y.ticks(5))
                    .join('line')
                    .attr('x1', margin.left)
                    .attr('x2', width - margin.right)
                    .attr('y1', d => y(d))
                    .attr('y2', d => y(d)));

            // Score line
            const scoreLine = d3.line()
                .x((d, i) => x(i))
                .y(d => y(d.score));

            svg.append('path')
                .datum(scoreHistory)
                .attr('fill', 'none')
                .attr('stroke', '#58a6ff')
                .attr('stroke-width', 1.5)
                .attr('d', scoreLine);

            // Best score line
            const bestLine = d3.line()
                .x((d, i) => x(i))
                .y(d => y(d.best_score));

            svg.append('path')
                .datum(scoreHistory)
                .attr('fill', 'none')
                .attr('stroke', '#3fb950')
                .attr('stroke-width', 2)
                .attr('d', bestLine);

            // Axes
            svg.append('g')
                .attr('transform', `translate(0,${{height - margin.bottom}})`)
                .call(d3.axisBottom(x).ticks(10))
                .attr('color', '#8b949e');

            svg.append('g')
                .attr('transform', `translate(${{margin.left}},0)`)
                .call(d3.axisLeft(y).ticks(5))
                .attr('color', '#8b949e');

            // Labels
            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height - 5)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text('Iteration');

            svg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', 15)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text('Score');

            // Legend
            const legend = svg.append('g')
                .attr('transform', `translate(${{width - 150}}, 30)`);

            legend.append('line')
                .attr('x1', 0).attr('x2', 20)
                .attr('y1', 0).attr('y2', 0)
                .attr('stroke', '#58a6ff').attr('stroke-width', 2);
            legend.append('text')
                .attr('x', 25).attr('y', 4)
                .attr('fill', '#8b949e').attr('font-size', '11px')
                .text('Score');

            legend.append('line')
                .attr('x1', 0).attr('x2', 20)
                .attr('y1', 20).attr('y2', 20)
                .attr('stroke', '#3fb950').attr('stroke-width', 2);
            legend.append('text')
                .attr('x', 25).attr('y', 24)
                .attr('fill', '#8b949e').attr('font-size', '11px')
                .text('Best Score');
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
            treeLayout(root);

            // Links
            g.selectAll('.link')
                .data(root.links())
                .join('path')
                .attr('class', 'link')
                .attr('d', d3.linkVertical()
                    .x(d => d.x)
                    .y(d => d.y));

            // Nodes
            const nodes = g.selectAll('.node')
                .data(root.descendants())
                .join('g')
                .attr('class', d => `node ${{d.data.is_best ? 'best' : ''}}`)
                .attr('transform', d => `translate(${{d.x}},${{d.y}})`);

            // Circles - bind click events directly to circles
            nodes.append('circle')
                .attr('r', d => d.data.is_best ? 10 : 7)
                .attr('fill', d => colorScale(d.data.score || 0))
                .attr('stroke', d => d.data.is_best ? '#ffd700' : d3.color(colorScale(d.data.score || 0)).darker())
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
                .text(d => d.data.name);
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

        // Tooltip functions
        function showTooltip(event, data) {{
            const tooltip = document.getElementById('tooltip');
            tooltip.innerHTML = `
                <h4>Program: ${{data.name}}</h4>
                <p><strong>Score:</strong> ${{(data.score || 0).toFixed(4)}}</p>
                <p><strong>Generation:</strong> ${{data.generation}}</p>
                <p><strong>Island:</strong> ${{data.island_id}}</p>
                ${{data.is_best ? '<p style="color: #ffd700;"><strong>Best Program</strong></p>' : ''}}
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

                // Feedback view
                const feedbackHtml = program.llm_feedback
                    ? `<p>${{escapeHtml(program.llm_feedback)}}</p>`
                    : '<p class="empty-state">No LLM feedback available</p>';
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

        // Render MAP-Elites heatmap
        function renderHeatmap() {{
            const container = document.getElementById('heatmap-container');
            const width = container.clientWidth;
            const height = 350;

            const svg = d3.select('#heatmap-container')
                .append('svg')
                .attr('width', width)
                .attr('height', height);

            if (mapElitesData.length === 0) {{
                svg.append('text')
                    .attr('x', width / 2)
                    .attr('y', height / 2)
                    .attr('text-anchor', 'middle')
                    .attr('fill', '#8b949e')
                    .text('No MAP-Elites data available');
                return;
            }}

            const margin = {{top: 40, right: 40, bottom: 60, left: 60}};
            const gridWidth = width - margin.left - margin.right;
            const gridHeight = height - margin.top - margin.bottom;

            // Determine grid dimensions
            const maxX = d3.max(mapElitesData, d => d.x) + 1;
            const maxY = d3.max(mapElitesData, d => d.y) + 1;
            const cellWidth = gridWidth / maxX;
            const cellHeight = gridHeight / maxY;

            const g = svg.append('g')
                .attr('transform', `translate(${{margin.left}},${{margin.top}})`);

            // Draw cells
            g.selectAll('.heatmap-cell')
                .data(mapElitesData)
                .join('rect')
                .attr('class', 'heatmap-cell')
                .attr('x', d => d.x * cellWidth)
                .attr('y', d => d.y * cellHeight)
                .attr('width', cellWidth - 2)
                .attr('height', cellHeight - 2)
                .attr('fill', d => colorScale(d.score))
                .attr('rx', 4)
                .on('click', (event, d) => showProgramDetail(d.program_id))
                .on('mouseover', (event, d) => {{
                    const tooltip = document.getElementById('tooltip');
                    tooltip.innerHTML = `
                        <h4>Cell (${{d.x}}, ${{d.y}})</h4>
                        <p><strong>Score:</strong> ${{d.score.toFixed(4)}}</p>
                        <p><strong>Program:</strong> ${{d.program_id.substring(0, 8)}}</p>
                        <p><strong>Island:</strong> ${{d.island_id}}</p>
                    `;
                    tooltip.style.display = 'block';
                    tooltip.style.left = (event.pageX + 10) + 'px';
                    tooltip.style.top = (event.pageY + 10) + 'px';
                }})
                .on('mouseout', hideTooltip);

            // Axes labels
            const dims = summaryStats.feature_dimensions || ['Dimension 1', 'Dimension 2'];

            svg.append('text')
                .attr('x', width / 2)
                .attr('y', height - 10)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text(dims[0] || 'Feature 1');

            svg.append('text')
                .attr('transform', 'rotate(-90)')
                .attr('x', -height / 2)
                .attr('y', 15)
                .attr('text-anchor', 'middle')
                .attr('fill', '#8b949e')
                .attr('font-size', '12px')
                .text(dims[1] || 'Feature 2');
        }}
    </script>
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

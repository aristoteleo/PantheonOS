"""
Pantheon-adapted worker for SingleCellAgent functionality
Uses omicverse for single-cell analysis within Pantheon's Agent framework
"""

import json
from typing import Dict, List, Any, Callable

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

from .apis import *  # noqa: F401,F403 - import API functions into registry

# Function registry
func2info = {
    # Core
    "get_cell_annotation_for_dataset": [get_cell_annotation_for_dataset, get_cell_annotation_for_dataset_doc],
    "get_trajectory_analysis_for_dataset": [get_trajectory_analysis_for_dataset, get_trajectory_analysis_for_dataset_doc],
    "get_differential_expression_for_groups": [get_differential_expression_for_groups, get_differential_expression_for_groups_doc],
    "get_pathway_enrichment_for_genes": [get_pathway_enrichment_for_genes, get_pathway_enrichment_for_genes_doc],
    "get_embedding_visualization_for_dataset": [get_embedding_visualization_for_dataset, get_embedding_visualization_for_dataset_doc],
    "get_gene_expression_visualization_for_dataset": [get_gene_expression_visualization_for_dataset, get_gene_expression_visualization_for_dataset_doc],
    # Expanded (OmicVerse plan)
    "get_qc_and_preprocessing_for_dataset": [get_qc_and_preprocessing_for_dataset, get_qc_and_preprocessing_for_dataset_doc],
    "get_clustering_for_dataset": [get_clustering_for_dataset, get_clustering_for_dataset_doc],
    "get_batch_integration_for_dataset": [get_batch_integration_for_dataset, get_batch_integration_for_dataset_doc],
    "get_cell_communication_for_dataset": [get_cell_communication_for_dataset, get_cell_communication_for_dataset_doc],
    "get_grn_analysis_for_dataset": [get_grn_analysis_for_dataset, get_grn_analysis_for_dataset_doc],
    "get_drug_response_for_dataset": [get_drug_response_for_dataset, get_drug_response_for_dataset_doc],
    "get_metacell_analysis_for_dataset": [get_metacell_analysis_for_dataset, get_metacell_analysis_for_dataset_doc],
}


class PantheonSingleCellAgent:
    """
    Single-cell analysis agent using omicverse and Pantheon's Agent capabilities
    """

    def __init__(self, function_names: List[str], agent_callback: Callable = None, show_progress: bool = True):
        """
        Initialize SingleCellAgent

        Args:
            function_names: List of function names to make available
            agent_callback: Callback function to Pantheon's Agent for LLM queries
            show_progress: Whether to show progress bars for operations
        """
        self.name2function = {function_name: func2info[function_name][0] for function_name in function_names}
        self.function_docs = [func2info[function_name][1] for function_name in function_names]
        self.agent_callback = agent_callback
        self.show_progress = show_progress
        self.console = Console()

        # Create function descriptions for the agent
        self.available_functions = {}
        for func_name in function_names:
            self.available_functions[func_name] = {
                "function": func2info[func_name][0],
                "doc": func2info[func_name][1],
            }

    def get_function_descriptions(self) -> str:
        """Get formatted function descriptions for the agent"""
        descriptions = []
        descriptions.append("Available functions for single-cell analysis:")

        for func_name, info in self.available_functions.items():
            doc = info["doc"]["description"]
            descriptions.append(f"\n- {func_name}: {doc}")

        return "\n".join(descriptions)

    def analyze_dataset(self, dataset_path: str, analysis_type: str = "comprehensive", research_question: str = None) -> str:
        """
        Perform comprehensive single-cell analysis using omicverse

        Args:
            dataset_path: Path to single-cell dataset (H5AD format)
            analysis_type: Type of analysis ("comprehensive", "annotation", "trajectory", "communication")
            research_question: Specific research question to address

        Returns:
            Analysis results with interpretation
        """

        if self.show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=self.console,
            ) as progress:
                task = progress.add_task("🧬 Analyzing single-cell dataset...", total=100)

                progress.update(task, advance=20, description="📝 Building analysis prompt...")

                system_prompt = f"""
You are a specialized single-cell RNA-seq analysis expert using omicverse.
Your task is to analyze the single-cell dataset and provide comprehensive insights.

Dataset: {dataset_path}
Analysis Type: {analysis_type}
Research Question: {research_question if research_question else "General exploration"}

Available omicverse-based functions:
"""

                function_descriptions = self.get_function_descriptions()

                content = f"""
Please perform a {analysis_type} analysis on the single-cell dataset at: {dataset_path}

Process:
1. Start with quality control assessment if needed
2. Perform cell type annotation
3. Conduct trajectory analysis for developmental studies
4. Analyze differential expression between groups
5. Perform pathway enrichment analysis
6. Generate visualizations to support findings
7. Provide biological interpretation and insights

{function_descriptions}

Begin the analysis step by step.
"""

                progress.update(task, advance=20, description="🤖 Processing with Agent...")

                if self.agent_callback:
                    try:
                        result = self.agent_callback(system_prompt + content)
                        progress.update(task, advance=40, description="📊 Processing results...")
                        final_result = self._process_analysis_result(result, dataset_path, analysis_type)
                        progress.update(task, advance=20, description="✅ Analysis complete!")
                        return final_result
                    except Exception as e:
                        progress.update(task, advance=60, description="❌ Agent error occurred")
                        return f"Analysis Error: {str(e)}"

                # Fallback: Direct function execution
                progress.update(task, advance=40, description="🔬 Using direct analysis...")
                result = self._direct_analysis_execution(dataset_path, analysis_type, progress, task)
                progress.update(task, advance=20, description="✅ Analysis complete!")
                return result
        else:
            # No progress bar version
            return self._direct_analysis_execution(dataset_path, analysis_type)

    def _direct_analysis_execution(self, dataset_path: str, analysis_type: str, progress=None, task=None) -> str:
        """Direct execution of analysis functions"""
        results = {}

        try:
            # Step 1: Cell annotation
            if "get_cell_annotation_for_dataset" in self.name2function:
                if progress:
                    progress.update(task, description="🏷️ Annotating cell types...")
                anno_result = self.name2function["get_cell_annotation_for_dataset"](dataset_path)
                results["annotation"] = anno_result

            # Step 2: Trajectory analysis (if requested)
            if analysis_type in ["comprehensive", "trajectory"]:
                if "get_trajectory_analysis_for_dataset" in self.name2function:
                    if progress:
                        progress.update(task, description="🌊 Analyzing trajectories...")
                    traj_result = self.name2function["get_trajectory_analysis_for_dataset"](dataset_path)
                    results["trajectory"] = traj_result

            # Step 3: Differential expression
            if "get_differential_expression_for_groups" in self.name2function:
                if progress:
                    progress.update(task, description="📈 Finding DE genes...")
                de_result = self.name2function["get_differential_expression_for_groups"](
                    dataset_path, group_key="cell_type"
                )
                results["differential_expression"] = de_result

            # Step 4: Pathway enrichment (if DE genes found)
            if "differential_expression" in results and results["differential_expression"].get("success"):
                top_genes = results["differential_expression"].get("top_genes", [])
                if top_genes and "get_pathway_enrichment_for_genes" in self.name2function:
                    if progress:
                        progress.update(task, description="🛤️ Analyzing pathways...")
                    pathway_result = self.name2function["get_pathway_enrichment_for_genes"](
                        ",".join(top_genes)
                    )
                    results["pathway_enrichment"] = pathway_result

            # Step 5: Visualization
            if analysis_type in ["comprehensive", "visualization"]:
                if "get_embedding_visualization_for_dataset" in self.name2function:
                    if progress:
                        progress.update(task, description="🎨 Creating visualizations...")
                    viz_result = self.name2function["get_embedding_visualization_for_dataset"](
                        dataset_path, color_by="cell_type", basis="umap"
                    )
                    results["visualization"] = viz_result

            return self._format_results(results, analysis_type)

        except Exception as e:
            return f"Analysis failed: {str(e)}"

    def _process_analysis_result(self, result: str, dataset_path: str, analysis_type: str) -> str:
        """Process and format agent output into report (placeholder)."""
        # In a real system, parse and validate the agent output
        return result

    def _format_results(self, results: Dict[str, Any], analysis_type: str) -> str:
        """Format analysis results into readable report"""
        report = []
        report.append(f"🧬 **SingleCellAgent Analysis Report**")
        report.append(f"Analysis Type: {analysis_type}\n")

        # Annotation results
        if "annotation" in results and results["annotation"].get("success"):
            anno = results["annotation"]
            report.append("## 🏷️ Cell Type Annotation")
            report.append(f"- Total cells: {anno.get('total_cells', 'N/A')}")
            report.append(f"- Unique cell types: {anno.get('unique_cell_types', 'N/A')}")
            report.append(f"- Method: {anno.get('method_used', 'N/A')}")
            if "cell_types" in anno:
                report.append("- Top cell types:")
                for ct, count in list(anno["cell_types"].items())[:5]:
                    report.append(f"  - {ct}: {count} cells")
            report.append("")

        # Trajectory results
        if "trajectory" in results and results["trajectory"].get("success"):
            traj = results["trajectory"]
            report.append("## 🌊 Trajectory Analysis")
            report.append(f"- Pseudotime range: {traj.get('pseudotime_range', 'N/A')}")
            report.append(f"- Trajectory branches: {traj.get('trajectory_branches', 'N/A')}")
            report.append(f"- Method: {traj.get('method_used', 'N/A')}")
            report.append("")

        # DE results
        if "differential_expression" in results and results["differential_expression"].get("success"):
            de = results["differential_expression"]
            report.append("## 📈 Differential Expression")
            report.append(f"- Total genes analyzed: {de.get('total_genes', 'N/A')}")
            report.append(f"- Significant genes: {de.get('significant_genes', 'N/A')}")
            report.append(f"- Method: {de.get('method_used', 'N/A')}")
            if "top_genes" in de and de["top_genes"]:
                report.append(f"- Top DE genes: {', '.join(de['top_genes'][:5])}")
            report.append("")

        # Pathway results
        if "pathway_enrichment" in results and results["pathway_enrichment"].get("success"):
            pathway = results["pathway_enrichment"]
            report.append("## 🛤️ Pathway Enrichment")
            report.append(f"- Significant pathways: {pathway.get('significant_pathways', 'N/A')}")
            report.append(f"- Database: {pathway.get('database_used', 'N/A')}")
            if "top_pathways" in pathway and pathway["top_pathways"]:
                report.append("- Top enriched pathways:")
                for p in pathway["top_pathways"][:3]:
                    report.append(f"  - {p}")
            report.append("")

        # Visualization results
        if "visualization" in results and results["visualization"].get("success"):
            viz = results["visualization"]
            report.append("## 🎨 Visualizations")
            report.append(f"- Plot type: {viz.get('plot_type', 'N/A')}")
            report.append(f"- Colored by: {viz.get('colored_by', 'N/A')}")
            report.append(f"- Saved to: {viz.get('saved_to', 'N/A')}")
            report.append("")

        report.append("---")
        report.append("*Analysis performed using omicverse through SingleCellAgent*")

        return "\n".join(report)


def create_singlecell_agent(function_names: List[str] = None, agent_callback: Callable = None, show_progress: bool = True) -> PantheonSingleCellAgent:
    """
    Factory function to create SingleCellAgent instance

    Args:
        function_names: List of functions to enable (uses DEFAULT_FUNCTIONS if None)
        agent_callback: Callback to Pantheon Agent
        show_progress: Whether to show progress bars

    Returns:
        PantheonSingleCellAgent instance
    """
    if function_names is None:
        function_names = DEFAULT_FUNCTIONS

    return PantheonSingleCellAgent(function_names, agent_callback, show_progress)


# Function sets for different analysis types
DEFAULT_FUNCTIONS = [
    "get_cell_annotation_for_dataset",
    "get_trajectory_analysis_for_dataset",
    "get_differential_expression_for_groups",
    "get_pathway_enrichment_for_genes",
    "get_embedding_visualization_for_dataset",
    "get_gene_expression_visualization_for_dataset",
]

ANNOTATION_FUNCTIONS = [
    "get_cell_annotation_for_dataset",
    "get_differential_expression_for_groups",
    "get_embedding_visualization_for_dataset",
]

TRAJECTORY_FUNCTIONS = [
    "get_trajectory_analysis_for_dataset",
    "get_cell_annotation_for_dataset",
    "get_differential_expression_for_groups",
]

VISUALIZATION_FUNCTIONS = [
    "get_embedding_visualization_for_dataset",
    "get_gene_expression_visualization_for_dataset",
]

COMPREHENSIVE_FUNCTIONS = DEFAULT_FUNCTIONS


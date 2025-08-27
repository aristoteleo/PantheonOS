"""SingleCellAgent ToolSet - Single-cell analysis using omicverse and Pantheon's Agent capabilities"""

from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import json
import os

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Prefer Pantheon ToolSet base; retain fallback for isolated contexts
try:
    from pantheon.toolsets.utils.toolset import ToolSet, tool
    from pantheon.toolsets.utils.log import logger
except ImportError:  # pragma: no cover - fallback only for standalone usage
    class ToolSet:
        def __init__(self, name, worker_params=None, **kwargs):
            self.name = name
            self.worker_params = worker_params or {}
            self.worker = type('Worker', (), {'functions': {}})()
    
    def tool(name):
        def decorator(func):
            func.tool_name = name
            return func
        return decorator
    
    class logger:  # noqa: N801
        @staticmethod
        def info(msg): print(f"INFO: {msg}")
        @staticmethod
        def error(msg): print(f"ERROR: {msg}")
        @staticmethod
        def warning(msg): print(f"WARNING: {msg}")


class SingleCellAgentToolSet(ToolSet):
    """Single-cell analysis toolset using omicverse and Pantheon-CLI's Agent.
    
    This toolset leverages omicverse's comprehensive single-cell analysis
    capabilities to perform sophisticated analyses including cell annotation,
    trajectory inference, differential expression, and visualization.
    """
    
    def __init__(
        self,
        name: str = "single_cell_agent",
        workspace_path: str = None,
        worker_params: dict | None = None,
        show_progress: bool = True,
        **kwargs,
    ):
        """Initialize SingleCellAgent toolset.
        
        Args:
            name: Name of the toolset
            workspace_path: Working directory for analysis
            worker_params: Worker parameters
            show_progress: Whether to show progress bars
            **kwargs: Additional arguments
        """
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.show_progress = show_progress
        self.console = Console()
        
        # Initialize analysis components
        self._initialize_agent()
        
    def _initialize_agent(self):
        """Initialize the SingleCellAgent with omicverse functions"""
        try:
            from .single_cell_agent_deps import create_singlecell_agent, DEFAULT_FUNCTIONS
            
            # Create agent with default functions
            self.agent = create_singlecell_agent(
                function_names=DEFAULT_FUNCTIONS,
                agent_callback=self._agent_callback,
                show_progress=self.show_progress
            )
            
            # Defer the initialization message until first use
            self._agent_ready = True
            
        except ImportError as e:
            logger.warning(f"Could not initialize full agent: {e}")
            self.agent = None
            self._agent_ready = False
    
    def _agent_callback(self, prompt: str) -> str:
        """Callback for agent to use Pantheon's LLM capabilities"""
        # This would connect to Pantheon's Agent system
        # For now, return a mock response
        return "Analysis complete. Results processed."
    
    @tool(name="SingleCellAgent")
    async def single_cell_agent(
        self,
        dataset: str,
        analysis_type: str = "comprehensive",
        output_format: str = "detailed",
        save_results: bool = False,
        visualize: bool = True,
        research_question: Optional[str] = None
    ) -> Dict[str, Any]:
        """Perform single-cell analysis using omicverse and Pantheon's Agent capabilities.
        
        This is the main tool that orchestrates various types of single-cell analysis
        using omicverse's comprehensive toolkit.
        
        Args:
            dataset: Path to single-cell dataset (H5AD format) or dataset identifier
            analysis_type: Type of analysis to perform:
                - "comprehensive": Full analysis including annotation, trajectory, DE, pathways
                - "annotation": Focus on cell type identification
                - "trajectory": Pseudotime and developmental analysis
                - "differential": Differential expression analysis
                - "visualization": Generate comprehensive plots
                - "qc": Basic QC + preprocessing (counts, genes, mt%, PCA/UMAP)
                - "clustering": Cluster cells (Leiden/Louvain) and summarize
                - "batch_integration": Correct batch effects (Harmony/Combat/etc.)
                - "communication": Cell-cell communication (CellPhoneDB via OmicVerse)
                - "grn": Gene regulatory network/TF activity (SCENIC/AUCell)
                - "drug": Drug response prediction (scDrug)
                - "metacell": Metacell construction/summary
                - "custom": Use research_question for specific analysis
            output_format: Format of results:
                - "detailed": Full detailed analysis
                - "summary": Concise summary
                - "structured": JSON-structured output
            save_results: Whether to save results to file
            visualize: Whether to generate visualizations
            research_question: Specific research question to address
            
        Returns:
            Dictionary containing:
                - success: Whether analysis completed successfully
                - dataset: Input dataset path
                - analysis_type: Type of analysis performed
                - results: Main analysis results
                - visualizations: Generated plots (if visualize=True)
                - metadata: Additional information about the analysis
        
        Examples:
            # Basic usage via CLI
            /bio SingleCellAgent pbmc_data.h5ad
            
            # Specific analysis type
            /bio SingleCellAgent data.h5ad --analysis_type annotation
            
            # Custom research question
            /bio SingleCellAgent data.h5ad --analysis_type custom --research_question "What are the developmental trajectories of T cells?"
        """
        
        try:
            # Show initialization message on first use
            if hasattr(self, '_agent_ready') and self._agent_ready and not hasattr(self, '_first_use_logged'):
                logger.info("🧬 SingleCellAgent initialized with omicverse")
                self._first_use_logged = True
            
            self.console.print(f"\n[bold cyan]🧬 SingleCellAgent Analysis Starting[/bold cyan]")
            self.console.print(f"Dataset: {dataset}")
            self.console.print(f"Analysis Type: {analysis_type}")
            
            # Validate dataset exists
            if not os.path.exists(dataset):
                return {
                    "success": False,
                    "error": f"Dataset not found: {dataset}",
                    "suggestion": "Please provide a valid path to an H5AD file"
                }
            
            # Create output directory if saving results
            if save_results:
                output_dir = self.workspace_path / "singlecell_results"
                output_dir.mkdir(exist_ok=True)
            else:
                output_dir = None
            
            # Perform analysis based on type
            if analysis_type == "comprehensive":
                results = await self._comprehensive_analysis(dataset, research_question, visualize, output_dir)
            elif analysis_type == "annotation":
                results = await self._annotation_analysis(dataset, visualize, output_dir)
            elif analysis_type == "trajectory":
                results = await self._trajectory_analysis(dataset, visualize, output_dir)
            elif analysis_type == "differential":
                results = await self._differential_analysis(dataset, visualize, output_dir)
            elif analysis_type == "visualization":
                results = await self._visualization_analysis(dataset, output_dir)
            elif analysis_type == "qc":
                results = await self._qc_preprocessing(dataset, output_dir)
            elif analysis_type == "clustering":
                results = await self._clustering_analysis(dataset, output_dir)
            elif analysis_type == "batch_integration":
                results = await self._batch_integration(dataset, output_dir)
            elif analysis_type == "communication":
                results = await self._communication_analysis(dataset, output_dir)
            elif analysis_type == "grn":
                results = await self._grn_analysis(dataset, output_dir)
            elif analysis_type == "drug":
                results = await self._drug_response_analysis(dataset, output_dir)
            elif analysis_type == "metacell":
                results = await self._metacell_analysis(dataset, output_dir)
            elif analysis_type == "custom" and research_question:
                results = await self._custom_analysis(dataset, research_question, visualize, output_dir)
            else:
                results = {
                    "error": f"Unknown analysis type: {analysis_type}",
                    "available_types": [
                        "comprehensive", "annotation", "trajectory", "differential", "visualization",
                        "qc", "clustering", "batch_integration", "communication", "grn", "drug", "metacell",
                        "custom"
                    ]
                }
            
            # Format output based on requested format
            if output_format == "summary":
                results = self._format_summary(results)
            elif output_format == "structured":
                results = self._format_structured(results)
            
            # Save results if requested
            if save_results and output_dir:
                results_file = output_dir / f"singlecell_analysis_{analysis_type}.json"
                with open(results_file, 'w') as f:
                    json.dump(results, f, indent=2, default=str)
                logger.info(f"Results saved to {results_file}")
            
            # Display results
            self._display_results(results, analysis_type)
            
            return {
                "success": True,
                "dataset": dataset,
                "analysis_type": analysis_type,
                "results": results,
                "output_format": output_format,
                "saved": save_results,
                "metadata": {
                    "tool": "SingleCellAgent",
                    "backend": "omicverse",
                    "workspace": str(self.workspace_path)
                }
            }
            
        except Exception as e:
            logger.error(f"SingleCellAgent analysis failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "dataset": dataset,
                "analysis_type": analysis_type
            }
    
    async def _comprehensive_analysis(self, dataset: str, research_question: str, visualize: bool, output_dir: Path) -> Dict[str, Any]:
        """Perform comprehensive 7-step analysis"""
        from .single_cell_agent_deps import func2info
        
        results = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console
        ) as progress:
            task = progress.add_task("🧬 Comprehensive Analysis...", total=7)
            
            # Step 1: Cell Annotation
            progress.update(task, advance=1, description="🏷️ Step 1: Cell Annotation...")
            if "get_cell_annotation_for_dataset" in func2info:
                # Prefer omicverse-first annotation
                anno_result = func2info["get_cell_annotation_for_dataset"][0](
                    dataset, method="pySCSA"
                )
                results["cell_annotation"] = anno_result
            
            # Step 2: Trajectory Analysis
            progress.update(task, advance=1, description="🌊 Step 2: Trajectory Analysis...")
            if "get_trajectory_analysis_for_dataset" in func2info:
                traj_result = func2info["get_trajectory_analysis_for_dataset"][0](
                    dataset, method="TrajInfer"
                )
                results["trajectory"] = traj_result
            
            # Step 3: Differential Expression
            progress.update(task, advance=1, description="📈 Step 3: Differential Expression...")
            if "get_differential_expression_for_groups" in func2info:
                de_result = func2info["get_differential_expression_for_groups"][0](
                    dataset, "cell_type", method="DCT"
                )
                results["differential_expression"] = de_result
            
            # Step 4: Pathway Enrichment
            progress.update(task, advance=1, description="🛤️ Step 4: Pathway Analysis...")
            if results.get("differential_expression", {}).get("top_genes"):
                genes = results["differential_expression"]["top_genes"]
                if "get_pathway_enrichment_for_genes" in func2info:
                    pathway_result = func2info["get_pathway_enrichment_for_genes"][0](",".join(genes))
                    results["pathway_enrichment"] = pathway_result
            
            # Step 5: Visualization
            if visualize:
                progress.update(task, advance=1, description="🎨 Step 5: Creating Visualizations...")
                viz_results = await self._create_visualizations(dataset, output_dir)
                results["visualizations"] = viz_results
            else:
                progress.update(task, advance=1)
            
            # Step 6: Integration
            progress.update(task, advance=1, description="🔄 Step 6: Integrating Results...")
            results["integration"] = self._integrate_results(results)
            
            # Step 7: Interpretation
            progress.update(task, advance=1, description="🧠 Step 7: Biological Interpretation...")
            results["interpretation"] = self._interpret_results(results, research_question)
        
        return results
    
    async def _annotation_analysis(self, dataset: str, visualize: bool, output_dir: Path) -> Dict[str, Any]:
        """Focus on cell type annotation"""
        from .single_cell_agent_deps import func2info
        
        results = {}
        
        # Perform annotation
        if "get_cell_annotation_for_dataset" in func2info:
            # Prefer omicverse-first method explicitly
            results["annotation"] = func2info["get_cell_annotation_for_dataset"][0](
                dataset, method="pySCSA"
            )
        
        # Create visualization if requested
        if visualize and "get_embedding_visualization_for_dataset" in func2info:
            save_path = str(output_dir / "cell_types_umap.png") if output_dir else None
            viz_result = func2info["get_embedding_visualization_for_dataset"][0](
                dataset, color_by="cell_type", basis="umap", save_path=save_path
            )
            results["visualization"] = viz_result
        
        return results
    
    async def _trajectory_analysis(self, dataset: str, visualize: bool, output_dir: Path) -> Dict[str, Any]:
        """Focus on trajectory and pseudotime analysis"""
        from .single_cell_agent_deps import func2info
        
        results = {}
        
        # Perform trajectory analysis
        if "get_trajectory_analysis_for_dataset" in func2info:
            # Prefer omicverse-first trajectory method
            results["trajectory"] = func2info["get_trajectory_analysis_for_dataset"][0](
                dataset, method="TrajInfer"
            )
        
        return results
    
    async def _differential_analysis(self, dataset: str, visualize: bool, output_dir: Path) -> Dict[str, Any]:
        """Focus on differential expression"""
        from .single_cell_agent_deps import func2info
        
        results = {}
        
        # Perform DE analysis
        if "get_differential_expression_for_groups" in func2info:
            # Prefer omicverse-first DE method (DCT); fallback to scanpy-based inside API if needed
            results["differential_expression"] = func2info["get_differential_expression_for_groups"][0](
                dataset, "cell_type", method="DCT"
            )
        
        # Pathway enrichment for DE genes
        if results.get("differential_expression", {}).get("top_genes"):
            genes = results["differential_expression"]["top_genes"]
            if "get_pathway_enrichment_for_genes" in func2info:
                results["pathway_enrichment"] = func2info["get_pathway_enrichment_for_genes"][0](",".join(genes))
        
        return results
    
    async def _visualization_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Generate visualizations"""
        from .single_cell_agent_deps import func2info
        
        results = {}
        
        if "get_embedding_visualization_for_dataset" in func2info:
            save_path = str(output_dir / "umap_by_celltype.png") if output_dir else None
            results["embedding_plot"] = func2info["get_embedding_visualization_for_dataset"][0](
                dataset, color_by="cell_type", basis="umap", save_path=save_path
            )
        
        if "get_gene_expression_visualization_for_dataset" in func2info:
            marker_genes = ["MS4A1", "NKG7", "CD14", "LYZ", "PPBP", "IL7R"]
            save_path = str(output_dir / "marker_violin.png") if output_dir else None
            results["gene_expression_plot"] = func2info["get_gene_expression_visualization_for_dataset"][0](
                dataset, genes=marker_genes, plot_type="violin", save_path=save_path
            )
        
        return results

    async def _qc_preprocessing(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Basic QC and preprocessing summary using Scanpy/OmicVerse"""
        from .single_cell_agent_deps import func2info
        # Reuse visualization and summary via dedicated API
        if "get_qc_and_preprocessing_for_dataset" in func2info:
            return func2info["get_qc_and_preprocessing_for_dataset"][0](
                dataset, save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "QC API not available"}

    async def _clustering_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Clustering analysis using Leiden/Louvain"""
        from .single_cell_agent_deps import func2info
        if "get_clustering_for_dataset" in func2info:
            return func2info["get_clustering_for_dataset"][0](
                dataset, method="leiden", resolution=1.0, save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "Clustering API not available"}

    async def _batch_integration(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Batch effect correction summary"""
        from .single_cell_agent_deps import func2info
        if "get_batch_integration_for_dataset" in func2info:
            return func2info["get_batch_integration_for_dataset"][0](
                dataset, batch_key="batch", method="harmony", save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "Batch integration API not available"}

    async def _communication_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Cell-cell communication analysis (CPDB via OmicVerse)"""
        from .single_cell_agent_deps import func2info
        if "get_cell_communication_for_dataset" in func2info:
            return func2info["get_cell_communication_for_dataset"][0](
                dataset, celltype_key="cell_type", save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "Communication API not available"}

    async def _grn_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Gene regulatory network/TF activity analysis (SCENIC/AUCell)"""
        from .single_cell_agent_deps import func2info
        if "get_grn_analysis_for_dataset" in func2info:
            return func2info["get_grn_analysis_for_dataset"][0](
                dataset, method="SCENIC", save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "GRN API not available"}

    async def _drug_response_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Drug response prediction (scDrug)"""
        from .single_cell_agent_deps import func2info
        if "get_drug_response_for_dataset" in func2info:
            return func2info["get_drug_response_for_dataset"][0](
                dataset, save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "Drug response API not available"}

    async def _metacell_analysis(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Metacell construction and summary"""
        from .single_cell_agent_deps import func2info
        if "get_metacell_analysis_for_dataset" in func2info:
            return func2info["get_metacell_analysis_for_dataset"][0](
                dataset, save_dir=str(output_dir) if output_dir else None
            )
        return {"success": False, "error": "Metacell API not available"}
    
    async def _custom_analysis(self, dataset: str, research_question: str, visualize: bool, output_dir: Path) -> Dict[str, Any]:
        """Custom analysis based on research question"""
        if self.agent:
            return self.agent.analyze_dataset(dataset, "custom", research_question)
        else:
            return {"error": "Agent not initialized for custom analysis"}
    
    async def _create_visualizations(self, dataset: str, output_dir: Path) -> Dict[str, Any]:
        """Create comprehensive visualizations"""
        return await self._visualization_analysis(dataset, output_dir)
    
    def _integrate_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Integrate results from different analyses"""
        integration = {
            "total_cells": 0,
            "cell_types_identified": 0,
            "significant_genes": 0,
            "enriched_pathways": 0
        }
        
        if "cell_annotation" in results and results["cell_annotation"].get("success"):
            integration["total_cells"] = results["cell_annotation"].get("total_cells", 0)
            integration["cell_types_identified"] = results["cell_annotation"].get("unique_cell_types", 0)
        
        if "differential_expression" in results and results["differential_expression"].get("success"):
            integration["significant_genes"] = results["differential_expression"].get("significant_genes", 0)
        
        if "pathway_enrichment" in results and results["pathway_enrichment"].get("success"):
            integration["enriched_pathways"] = results["pathway_enrichment"].get("significant_pathways", 0)
        
        return integration
    
    def _interpret_results(self, results: Dict[str, Any], research_question: str = None) -> str:
        """Generate biological interpretation"""
        interpretation = []
        
        interpretation.append("## Biological Interpretation\n")
        
        if research_question:
            interpretation.append(f"**Research Question**: {research_question}\n")
        
        # Interpret annotation results
        if "cell_annotation" in results and results["cell_annotation"].get("success"):
            anno = results["cell_annotation"]
            interpretation.append(f"**Cell Composition**: Identified {anno.get('unique_cell_types', 0)} distinct cell types")
            interpretation.append(f"in {anno.get('total_cells', 0)} cells.\n")
        
        # Interpret trajectory results
        if "trajectory" in results and results["trajectory"].get("success"):
            traj = results["trajectory"]
            interpretation.append(f"**Developmental Dynamics**: Trajectory analysis reveals")
            interpretation.append(f"{traj.get('trajectory_branches', 1)} developmental branches.\n")
        
        # Interpret DE results
        if "differential_expression" in results and results["differential_expression"].get("success"):
            de = results["differential_expression"]
            interpretation.append(f"**Gene Expression**: Found {de.get('significant_genes', 0)} differentially")
            interpretation.append(f"expressed genes across cell types.\n")
        
        # Interpret pathway results
        if "pathway_enrichment" in results and results["pathway_enrichment"].get("success"):
            pathway = results["pathway_enrichment"]
            interpretation.append(f"**Functional Enrichment**: {pathway.get('significant_pathways', 0)} pathways")
            interpretation.append(f"significantly enriched, suggesting active biological processes.\n")
        
        return " ".join(interpretation)
    
    def _format_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Format results as concise summary"""
        summary = {
            "key_findings": [],
            "statistics": {}
        }
        
        # Extract key findings
        if "cell_annotation" in results and results["cell_annotation"].get("success"):
            summary["key_findings"].append(
                f"Identified {results['cell_annotation'].get('unique_cell_types', 0)} cell types"
            )
        
        if "trajectory" in results and results["trajectory"].get("success"):
            summary["key_findings"].append(
                f"Found {results['trajectory'].get('trajectory_branches', 1)} developmental trajectories"
            )
        
        if "differential_expression" in results and results["differential_expression"].get("success"):
            summary["key_findings"].append(
                f"{results['differential_expression'].get('significant_genes', 0)} significant DE genes"
            )
        
        # Add statistics
        if "integration" in results:
            summary["statistics"] = results["integration"]
        
        return summary
    
    def _format_structured(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Format results as structured JSON"""
        import json as _json
        return _json.loads(_json.dumps(results, default=str))
    
    def _display_results(self, results: Dict[str, Any], analysis_type: str):
        """Display results in console"""
        
        # Create summary table
        table = Table(title=f"SingleCellAgent Analysis Results - {analysis_type}")
        table.add_column("Analysis Component", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Key Results", style="white")
        
        # Add rows for each analysis component
        for component, data in results.items():
            if isinstance(data, dict) and data.get("success"):
                status = "✅ Complete"
                if component == "cell_annotation":
                    key_result = f"{data.get('unique_cell_types', 0)} cell types"
                elif component == "trajectory":
                    key_result = f"{data.get('trajectory_branches', 1)} branches"
                elif component == "differential_expression":
                    key_result = f"{data.get('significant_genes', 0)} DE genes"
                elif component == "pathway_enrichment":
                    key_result = f"{data.get('significant_pathways', 0)} pathways"
                else:
                    key_result = "Completed"
            else:
                status = "⏭️ Skipped"
                key_result = "-"
            
            if component not in {"integration", "interpretation"}:  # skip meta sections in table rows
                table.add_row(component, status, key_result)
        
        self.console.print(table)
        
        # Print interpretation if available
        if isinstance(results, dict) and results.get("interpretation"):
            self.console.print(Panel.fit(results["interpretation"], title="Interpretation", border_style="magenta"))


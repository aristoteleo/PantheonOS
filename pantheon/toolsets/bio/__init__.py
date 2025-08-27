"""Bio Toolsets Manager - Unified interface for all bioinformatics analysis tools"""

from pathlib import Path
from typing import Dict, List, Any, Optional
from rich.table import Table
from rich.panel import Panel
from ..utils.log import logger
from ..utils.toolset import ToolSet, tool

# Direct imports - hard management approach
from .atac import ATACSeqToolSet
from .scatac import ScATACSeqToolSet
from .scrna import ScRNASeqToolSet
from .rna import RNASeqToolSet
from .gene_agent import GeneAgentToolSet
from .single_cell_agent import SingleCellAgentToolSet
from .database_query import DatabaseQueryToolSet
from .dock import MolecularDockingToolSet
from .hic import HiCToolSet
from .spatial import SpatialToolSet

# Optional: SingleCellAgent (external repo folder). We'll attempt to import it
# and gracefully skip if unavailable.
import sys
from pathlib import Path

class BioToolsetManager(ToolSet):
    """
    Bio Toolset Manager - Provides unified interface for all bio analysis tools
    
    Hard management approach - directly imports and exposes all bio tools
    
    Commands:
    - /bio atac init - Initialize ATAC project
    - /bio atac upstream <folder> - Run ATAC upstream analysis
    - /bio scatac init - Initialize scATAC project
    - /bio scatac upstream <folder> - Run scATAC upstream analysis
    - /bio scrna init - Initialize scRNA project
    - /bio scrna load_and_inspect_data <file> - Load and inspect scRNA data
    - /bio gene_agent analyze_gene_set <genes> - Analyze gene set with GeneAgent
    - /bio gene_agent verify_gene_claims <claims> - Verify biological claims
    - /bio gene_agent get_gene_info <gene> - Get gene information
    - /bio database_query <natural language> - Query public databases (auto-route)
    - /bio database_query list_sources - List supported sources
    - /bio spatial run_spatial_workflow <workflow_type> - Run spatial workflow

    """
    
    def __init__(
        self,
        name: str = "bio",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs,
    ):
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.launch_directory = Path(launch_directory) if launch_directory else Path.cwd()
        
        # Hard management - directly initialize all bio tools
        # Filter kwargs to avoid passing unsupported parameters to ATAC
        atac_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.atac = ATACSeqToolSet(
            name="atac",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **atac_kwargs
        )
        
        # Filter out launch_directory from kwargs to avoid duplication
        scatac_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.scatac = ScATACSeqToolSet(
            name="scatac",
            workspace_path=workspace_path,
            launch_directory=launch_directory,
            worker_params=worker_params,
            **scatac_kwargs
        )
        
        # Initialize scrna toolset
        scrna_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.scrna = ScRNASeqToolSet(
            name="scrna",
            workspace_path=workspace_path,
            launch_directory=launch_directory,
            worker_params=worker_params,
            **scrna_kwargs
        )
        
        # Initialize RNA-seq toolset
        rna_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.rna = RNASeqToolSet(
            name="rna",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **rna_kwargs
        )
        
        # Initialize GeneAgent toolset
        gene_agent_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.gene_agent = GeneAgentToolSet(
            name="gene_agent",
            worker_params=worker_params,
            **gene_agent_kwargs
        )

        # Initialize SingleCellAgent toolset (embedded, no external import)
        sc_agent_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        try:
            self.single_cell_agent = SingleCellAgentToolSet(
                name="single_cell_agent",
                workspace_path=workspace_path,
                worker_params=worker_params,
                **sc_agent_kwargs
            )
        except Exception:
            self.single_cell_agent = None

        # Initialize DatabaseQuery toolset (OmicVerse DataCollect wrappers)
        dbq_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        try:
            self.database_query = DatabaseQueryToolSet(
                name="database_query",
                worker_params=worker_params,
                **dbq_kwargs
            )
        except Exception:
            self.database_query = None
        
        # Initialize Molecular Docking toolset
        dock_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.dock = MolecularDockingToolSet(
            name="dock",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **dock_kwargs
        )
        
        # Initialize Hi-C toolset
        hic_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.hic = HiCToolSet(
            name="hic",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **hic_kwargs
        )

        # Initialize Spatial toolset
        spatial_kwargs = {k: v for k, v in kwargs.items() if k != 'launch_directory'}
        self.spatial = SpatialToolSet(
            name="spatial",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **spatial_kwargs
        )
        # Copy all ATAC tools to this manager - simple and direct
        for name, (func, desc) in self.atac.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all scATAC tools to this manager - simple and direct
        for name, (func, desc) in self.scatac.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all scRNA tools to this manager - simple and direct
        for name, (func, desc) in self.scrna.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all RNA tools to this manager - simple and direct
        for name, (func, desc) in self.rna.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all GeneAgent tools to this manager
        for name, (func, desc) in self.gene_agent.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)

        # Copy all SingleCellAgent tools to this manager
        if self.single_cell_agent is not None:
            for name, (func, desc) in self.single_cell_agent.worker.functions.items():
                self.worker.functions[name] = (func, desc)
                setattr(self, name, func)

        # Copy all DatabaseQuery tools to this manager
        if self.database_query is not None:
            for name, (func, desc) in self.database_query.worker.functions.items():
                self.worker.functions[name] = (func, desc)
                setattr(self, name, func)
        
        # Track loaded tools for reporting
        self.loaded_tools = {
            "atac": self.atac,
            "scatac": self.scatac,
            "scrna": self.scrna,
            "gene_agent": self.gene_agent,
        }
        if self.single_cell_agent is not None:
            self.loaded_tools["single_cell_agent"] = self.single_cell_agent
        if self.database_query is not None:
            self.loaded_tools["database_query"] = self.database_query
        self.available_tools = [
            "atac",
            "scatac",
            "scrna",
            "gene_agent",
            "single_cell_agent",
            "database_query",
        ]
        # Copy all Dock tools to this manager
        for name, (func, desc) in self.dock.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all HiC tools to this manager
        for name, (func, desc) in self.hic.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy all Spatial tools to this manager
        for name, (func, desc) in self.spatial.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        self.loaded_tools = {"atac": self.atac, "scatac": self.scatac, "scrna": self.scrna, "rna": self.rna, 
                             "gene_agent": self.gene_agent, "dock": self.dock, "hic": self.hic, "spatial": self.spatial}
        self.available_tools = ["atac", "scatac", "scrna", "rna", "gene_agent", "dock", "hic", "spatial"]
    
        # Try to load external SingleCellAgent toolset if present
        self._try_register_single_cell_agent(workspace_path, worker_params, **kwargs)
    
    
    @tool
    def list(self) -> str:
        """List all available bio analysis tools"""
        
        logger.info(f"\n🧬 [bold cyan]Bio Analysis Tools[/bold cyan]\n")
        
        if not self.available_tools:
            return "No bio tools available"
        
        # Create tools table
        tools_table = Table(title="Available Bio Tools")
        tools_table.add_column("Tool", style="cyan")
        tools_table.add_column("Status", style="green") 
        tools_table.add_column("Description", style="dim")
        
        # Reorder to prefer omicverse/SingleCellAgent at the top when present
        tools_order = list(self.available_tools)
        if "single_cell_agent" in tools_order:
            tools_order.remove("single_cell_agent")
            tools_order.insert(0, "single_cell_agent")

        for tool_name in tools_order:
            if tool_name in self.loaded_tools:
                status = "✅ Loaded"
                description = self._get_tool_description(tool_name)
            else:
                status = "❌ Failed"
                description = "Failed to load"
            
            tools_table.add_row(tool_name.upper(), status, description)
        
        logger.info("", rich=tools_table)
        
        
        return f"Found {len(self.available_tools)} bio tools ({len(self.loaded_tools)} loaded successfully)"
    
    def _get_tool_description(self, tool_name: str) -> str:
        """Get description for a bio tool"""
        descriptions = {
            "atac": "ATAC-seq chromatin accessibility analysis",
            "scatac": "Single-cell ATAC-seq analysis with cellranger-atac",
            "rna": "RNA-seq transcriptome analysis", 
            "chipseq": "ChIP-seq protein-DNA interaction analysis",
            "scrna": "Single-cell RNA-seq analysis",
            "wgs": "Whole genome sequencing analysis",
            "gene_agent": "GeneAgent cascade system for gene set analysis",
            "single_cell_agent": "End-to-end single-cell analysis via OmicVerse (annotation, DE, trajectory, viz)",
            "database_query": "Query public bio databases via OmicVerse DataCollect",
            "dock": "Molecular docking for protein-ligand interactions",
            "hic": "Hi-C chromosome conformation capture analysis",
            "spatial": "Spatial transcriptomics analysis"
        }
        return descriptions.get(tool_name, "Bioinformatics analysis tool")
    
    @tool
    def info(self, tool_name: str) -> str:
        """Get detailed information about a specific bio tool"""
        
        if tool_name not in self.available_tools:
            available = ", ".join(self.available_tools)
            return f"Tool '{tool_name}' not found. Available tools: {available}"
        
        if tool_name not in self.loaded_tools:
            return f"Tool '{tool_name}' failed to load"
        
        tool_instance = self.loaded_tools[tool_name]
        
        # Get tool methods
        tool_methods = []
        for method_name in dir(tool_instance):
            method = getattr(tool_instance, method_name)
            if hasattr(method, '_is_tool'):
                tool_methods.append(method_name)
        
        info_text = f"""
🧬 {tool_name.upper()} Analysis Tool

Description: {self._get_tool_description(tool_name)}
Status: {"✅ Loaded" if tool_name in self.loaded_tools else "❌ Failed"}
Methods: {len(tool_methods)} available

Available Commands:
"""
        
        for method in sorted(tool_methods):
            info_text += f"• /bio {tool_name} {method}\n"
        
        return info_text
    
    @tool
    def help(self, tool_name: Optional[str] = None) -> str:
        """Get help information for bio tools"""
        
        if tool_name is None:
            return self.list()
        
        return self.info(tool_name)
      
    def _try_register_single_cell_agent(self, workspace_path: str, worker_params: dict | None, **kwargs):
        """Attempt to import and register SingleCellAgentToolSet if available.

        We search relative to the launch directory for a sibling 'single_cell_agent' folder
        and temporarily add that parent to sys.path for import.
        """
        # Skip if already loaded
        if "single_cell_agent" in self.loaded_tools:
            return

        # Attempt direct import first
        def _import_sca():
            from single_cell_agent import SingleCellAgentToolSet  # type: ignore
            return SingleCellAgentToolSet

        sca_cls = None
        try:
            sca_cls = _import_sca()
        except Exception:
            # Try to locate the folder from launch_directory and its parents
            candidates = []
            try:
                launch_dir = self.launch_directory
                candidates = [launch_dir] + list(launch_dir.parents)
            except Exception:
                candidates = []
            for base in candidates:
                sca_dir = base / "single_cell_agent"
                if sca_dir.exists() and sca_dir.is_dir():
                    sys.path.insert(0, str(base))
                    try:
                        sca_cls = _import_sca()
                        break
                    except Exception:
                        continue

        if sca_cls is None:
            return  # Not available; silent skip

        try:
            sca = sca_cls(
                name="single_cell_agent",
                workspace_path=workspace_path,
                worker_params=worker_params,
                **kwargs
            )
            # Register its tools
            for name, (func, desc) in sca.worker.functions.items():
                self.worker.functions[name] = (func, desc)
                setattr(self, name, func)
            self.loaded_tools["single_cell_agent"] = sca
            if "single_cell_agent" not in self.available_tools:
                self.available_tools.append("single_cell_agent")
            logger.info("✅ Loaded SingleCellAgent toolset")
        except Exception:
            # Don't break the bio manager if SingleCellAgent fails
            pass

__all__ = ['BioToolsetManager']

"""RNA-seq Analysis Toolset for Pantheon CLI"""

from .upstream import RNASeqUpstreamToolSet
from .analysis import RNASeqAnalysisToolSet
from ...utils.toolset import ToolSet

# For backward compatibility, create a combined toolset that inherits from ToolSet
class RNASeqToolSet(ToolSet):
    """Combined RNA-seq Analysis Toolset - Provides both upstream and downstream functionality"""
    
    def __init__(
        self,
        name: str = "rna",
        workspace_path: str = None,
        worker_params: dict = None,
        **kwargs,
    ):
        # Initialize the parent ToolSet
        super().__init__(name, worker_params, **kwargs)
        
        # Create the upstream and analysis toolsets
        self.upstream = RNASeqUpstreamToolSet(
            name=f"{name}_upstream",
            workspace_path=workspace_path,
            worker_params=worker_params,
            **kwargs
        )
        self.analysis = RNASeqAnalysisToolSet(
            name=f"{name}_analysis", 
            workspace_path=workspace_path,
            worker_params=worker_params,
            **kwargs
        )
        
        # Set workspace path for compatibility
        self.workspace_path = workspace_path
        
        # Expose all upstream and downstream tools at the top level
        self._expose_tools()
    
    def _expose_tools(self):
        """Expose all tools from upstream and analysis toolsets"""
        # Copy tools from upstream worker
        for name, (func, desc) in self.upstream.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)
        
        # Copy tools from analysis worker  
        for name, (func, desc) in self.analysis.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)

__all__ = ['RNASeqToolSet', 'RNASeqUpstreamToolSet', 'RNASeqAnalysisToolSet']
from .analysis_bin2cell import SpatialBin2CellAnalysisToolSet
from ...utils.toolset import ToolSet


class SpatialToolSet(ToolSet):
    """Combined Spatial Analysis Toolset - Provides both upstream and downstream functionality"""
    
    def __init__(self, name: str = "spatial", workspace_path: str = None, 
                 launch_directory: str = None, worker_params: dict = None, **kwargs):
        super().__init__(name, worker_params, **kwargs)
        self.analysis = SpatialBin2CellAnalysisToolSet(
            name=f"{name}_analysis", 
            workspace_path=workspace_path,
            launch_directory=launch_directory,
            worker_params=worker_params,
            **kwargs
        )
        
        # Set workspace path for compatibility
        self.workspace_path = workspace_path
        self.launch_directory = launch_directory

        # Expose pipeline config from upstream for compatibility
        self.pipeline_config = self.analysis.pipeline_config
        
        
        # Expose all upstream and downstream tools at the top level
        self._expose_tools()


    def _expose_tools(self):
        """Expose all tools from upstream and analysis toolsets"""
        # Copy tools from analysis worker  
        for name, (func, desc) in self.analysis.worker.functions.items():
            self.worker.functions[name] = (func, desc)
            setattr(self, name, func)


__all__ = ['SpatialToolSet']
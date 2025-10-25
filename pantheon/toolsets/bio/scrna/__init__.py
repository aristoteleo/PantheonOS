"""Single-cell RNA-seq Analysis Toolset for Pantheon CLI"""

from .upstream import ScRNASeqUpstreamToolSet
from .analysis_anno import ScRNASeqAnalysisToolSet
from pantheon.toolset import ToolSet


class ScRNASeqToolSet(ToolSet):
    """Combined Single-cell RNA-seq Analysis Toolset - Provides both upstream and downstream functionality"""

    def __init__(
        self,
        name: str = "scrna",
        workspace_path: str = None,
        launch_directory: str = None,
        **kwargs,
    ):
        # Initialize the parent ToolSet
        super().__init__(name, **kwargs)

        # Create the upstream and analysis toolsets
        self.upstream = ScRNASeqUpstreamToolSet(
            name=f"{name}_upstream",
            workspace_path=workspace_path,
            launch_directory=launch_directory,
            **kwargs,
        )
        self.analysis = ScRNASeqAnalysisToolSet(
            name=f"{name}_analysis",
            workspace_path=workspace_path,
            launch_directory=launch_directory,
            **kwargs,
        )

        # Set workspace path for compatibility
        self.workspace_path = workspace_path

        # Expose pipeline config from upstream for compatibility
        self.pipeline_config = self.upstream.pipeline_config

        # Expose all upstream and downstream tools at the top level
        self._expose_tools()

    def _expose_tools(self):
        """Expose all tools from upstream and analysis toolsets"""
        # Copy tools from upstream worker
        for name, (func, desc) in self.upstream..functions.items():
            self..functions[name] = (func, desc)
            setattr(self, name, func)

        # Copy tools from analysis worker
        for name, (func, desc) in self.analysis..functions.items():
            self..functions[name] = (func, desc)
            setattr(self, name, func)


__all__ = ["ScRNASeqToolSet", "ScRNASeqUpstreamToolSet", "ScRNASeqAnalysisToolSet"]

"""Enhanced Hi-C Analysis Toolset for Pantheon CLI"""

from .upstream import HiCUpstreamToolSet
from .analysis import HiCAnalysisToolSet
from pantheon.toolset import ToolSet


# For backward compatibility, create a combined toolset that inherits from ToolSet
class HiCToolSet(ToolSet):
    """Combined Hi-C Analysis Toolset - Provides both upstream and downstream functionality"""

    def __init__(
        self,
        name: str = "hic",
        workspace_path: str = None,
        **kwargs,
    ):
        # Initialize the parent ToolSet
        super().__init__(name, **kwargs)

        # Create the upstream and analysis toolsets
        self.upstream = HiCUpstreamToolSet(
            name=f"{name}_upstream", workspace_path=workspace_path, **kwargs
        )
        self.analysis = HiCAnalysisToolSet(
            name=f"{name}_analysis", workspace_path=workspace_path, **kwargs
        )

        # Set workspace path for compatibility
        self.workspace_path = workspace_path

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


__all__ = ["HiCToolSet", "HiCUpstreamToolSet", "HiCAnalysisToolSet"]

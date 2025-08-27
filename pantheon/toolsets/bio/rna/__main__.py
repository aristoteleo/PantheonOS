"""RNA-seq Analysis Toolset Main Entry Point"""

from .upstream import RNASeqUpstreamToolSet
from .analysis import RNASeqAnalysisToolSet

if __name__ == "__main__":
    print("RNA-seq Analysis Toolset")
    print("Available toolsets:")
    print("- RNASeqUpstreamToolSet: RNA-seq upstream analysis (QC, alignment, quantification)")
    print("- RNASeqAnalysisToolSet: RNA-seq downstream analysis (DE, pathways, visualization)")
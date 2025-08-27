"""
SingleCellAgent Dependencies embedded for Pantheon-CLI usage with omicverse
"""

from .worker_pantheon import (
    PantheonSingleCellAgent,
    create_singlecell_agent,
    DEFAULT_FUNCTIONS,
    ANNOTATION_FUNCTIONS,
    TRAJECTORY_FUNCTIONS,
    VISUALIZATION_FUNCTIONS,
    COMPREHENSIVE_FUNCTIONS,
    func2info,
)

from .apis import *  # re-export API entrypoints

__all__ = [
    'PantheonSingleCellAgent',
    'create_singlecell_agent',
    'DEFAULT_FUNCTIONS',
    'ANNOTATION_FUNCTIONS',
    'TRAJECTORY_FUNCTIONS',
    'VISUALIZATION_FUNCTIONS',
    'COMPREHENSIVE_FUNCTIONS',
    'func2info',
    # API functions
    'get_cell_annotation_for_dataset',
    'get_trajectory_analysis_for_dataset',
    'get_differential_expression_for_groups',
    'get_pathway_enrichment_for_genes',
    'get_embedding_visualization_for_dataset',
    'get_gene_expression_visualization_for_dataset',
]


"""SingleCellAgent API functions"""

from .cell_annotation_analysis import get_cell_annotation_for_dataset, get_cell_annotation_for_dataset_doc
from .trajectory_analysis import get_trajectory_analysis_for_dataset, get_trajectory_analysis_for_dataset_doc
from .differential_expression import get_differential_expression_for_groups, get_differential_expression_for_groups_doc
from .pathway_enrichment import get_pathway_enrichment_for_genes, get_pathway_enrichment_for_genes_doc
from .visualization_analysis import (
    get_embedding_visualization_for_dataset, get_embedding_visualization_for_dataset_doc,
    get_gene_expression_visualization_for_dataset, get_gene_expression_visualization_for_dataset_doc,
)
from .qc_preprocessing import get_qc_and_preprocessing_for_dataset, get_qc_and_preprocessing_for_dataset_doc
from .clustering_analysis import get_clustering_for_dataset, get_clustering_for_dataset_doc
from .batch_integration import get_batch_integration_for_dataset, get_batch_integration_for_dataset_doc
from .communication_analysis import get_cell_communication_for_dataset, get_cell_communication_for_dataset_doc
from .grn_analysis import get_grn_analysis_for_dataset, get_grn_analysis_for_dataset_doc
from .drug_response_analysis import get_drug_response_for_dataset, get_drug_response_for_dataset_doc
from .metacell_analysis import get_metacell_analysis_for_dataset, get_metacell_analysis_for_dataset_doc

__all__ = [
    'get_cell_annotation_for_dataset', 'get_cell_annotation_for_dataset_doc',
    'get_trajectory_analysis_for_dataset', 'get_trajectory_analysis_for_dataset_doc',
    'get_differential_expression_for_groups', 'get_differential_expression_for_groups_doc',
    'get_pathway_enrichment_for_genes', 'get_pathway_enrichment_for_genes_doc',
    'get_embedding_visualization_for_dataset', 'get_embedding_visualization_for_dataset_doc',
    'get_gene_expression_visualization_for_dataset', 'get_gene_expression_visualization_for_dataset_doc',
    'get_qc_and_preprocessing_for_dataset', 'get_qc_and_preprocessing_for_dataset_doc',
    'get_clustering_for_dataset', 'get_clustering_for_dataset_doc',
    'get_batch_integration_for_dataset', 'get_batch_integration_for_dataset_doc',
    'get_cell_communication_for_dataset', 'get_cell_communication_for_dataset_doc',
    'get_grn_analysis_for_dataset', 'get_grn_analysis_for_dataset_doc',
    'get_drug_response_for_dataset', 'get_drug_response_for_dataset_doc',
    'get_metacell_analysis_for_dataset', 'get_metacell_analysis_for_dataset_doc',
]


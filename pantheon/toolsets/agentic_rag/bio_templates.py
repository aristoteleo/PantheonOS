"""
Bioinformatics code templates for common analysis workflows
These templates provide starting points that the LLM can expand upon
"""

ANALYSIS_TEMPLATES = {
    "scrna_standard": """
import scanpy as sc
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Set up scanpy settings
sc.settings.verbosity = 3
sc.settings.set_figure_params(dpi=80, facecolor='white')

# Load data
adata = sc.read_h5ad('{data_path}')
print(f"Initial data shape: {adata.shape}")

# Basic quality control
sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False, inplace=True)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)

# QC plots
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
sc.pl.violin(adata, ['n_genes_by_counts', 'total_counts', 'pct_counts_mt'],
             jitter=0.4, multi_panel=True, ax=axes)
plt.show()

# Filter cells and genes
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata = adata[adata.obs.pct_counts_mt < 5, :]
print(f"After QC: {adata.shape}")

# Normalization and log transformation
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# Find highly variable genes
sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
adata.raw = adata
adata = adata[:, adata.var.highly_variable]

# Scale data
sc.pp.scale(adata, max_value=10)

# Principal component analysis
sc.tl.pca(adata, svd_solver='arpack')
sc.pl.pca_variance_ratio(adata, log=True, n_pcs=50)

# Compute neighborhood graph
sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)

# Perform clustering
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# Visualization
sc.pl.umap(adata, color=['leiden'], legend_loc='on data', title='Leiden clustering')
""",

    "differential_expression": """
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt

# Load preprocessed data
adata = sc.read_h5ad('{data_path}')

# Set the grouping variable
groupby = '{groupby_var}'  # e.g., 'leiden', 'cell_type', 'condition'

# Find marker genes for each group
sc.tl.rank_genes_groups(adata, groupby, method='wilcoxon', key_added='rank_genes')

# Plot top marker genes
sc.pl.rank_genes_groups(adata, n_genes=25, sharey=False, key='rank_genes')

# Get results as DataFrame
result = adata.uns['rank_genes']
groups = result['names'].dtype.names
df_list = []
for group in groups:
    df = pd.DataFrame({
        'gene': result['names'][group],
        'score': result['scores'][group],
        'pval': result['pvals'][group],
        'pval_adj': result['pvals_adj'][group],
        'logfc': result['logfoldchanges'][group],
        'group': group
    })
    df_list.append(df)

markers_df = pd.concat(df_list)
markers_df.to_csv('differential_expression_results.csv', index=False)
print(f"Saved {len(markers_df)} marker genes to differential_expression_results.csv")

# Visualize top markers as heatmap
sc.pl.rank_genes_groups_heatmap(adata, n_genes=5, key='rank_genes', show_gene_labels=True)
""",

    "trajectory_analysis": """
import scanpy as sc
import scvelo as scv
import pandas as pd
import matplotlib.pyplot as plt

# Load data
adata = sc.read_h5ad('{data_path}')

# Preprocessing for trajectory
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
sc.pp.scale(adata, max_value=10)

# PCA and neighbors
sc.tl.pca(adata, svd_solver='arpack')
sc.pp.neighbors(adata, n_neighbors=30, n_pcs=40)

# Compute UMAP
sc.tl.umap(adata)

# Diffusion map for trajectory
sc.tl.diffmap(adata)

# Compute PAGA trajectory
sc.tl.leiden(adata, resolution=0.5)
sc.tl.paga(adata, groups='leiden')

# Plot PAGA
sc.pl.paga(adata, color=['leiden'])

# Force-directed graph
sc.tl.draw_graph(adata, init_pos='paga')
sc.pl.draw_graph(adata, color=['leiden'], legend_loc='on data')

# Pseudotime using diffusion
adata.uns['iroot'] = np.flatnonzero(adata.obs['leiden'] == '0')[0]  # Set root cell
sc.tl.dpt(adata)

# Plot pseudotime
sc.pl.draw_graph(adata, color=['dpt_pseudotime'], legend_loc='on data')
sc.pl.umap(adata, color=['dpt_pseudotime'])
""",

    "batch_integration": """
import scanpy as sc
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import harmonypy

# Load multiple datasets
datasets = []
batch_names = []

# Example for multiple files
for i, file_path in enumerate({data_paths}):
    adata = sc.read_h5ad(file_path)
    adata.obs['batch'] = f'batch_{i}'
    datasets.append(adata)
    batch_names.append(f'batch_{i}')

# Concatenate datasets
adata = sc.concat(datasets, join='outer', label='batch', keys=batch_names)
print(f"Combined data shape: {adata.shape}")

# Standard preprocessing
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
adata = adata[adata.obs.pct_counts_mt < 5, :]

# Normalize and find HVGs
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5, batch_key='batch')
adata.raw = adata
adata = adata[:, adata.var.highly_variable]

# Scale
sc.pp.scale(adata, max_value=10)

# PCA
sc.tl.pca(adata, svd_solver='arpack')

# Run Harmony for batch correction
ho = harmonypy.run_harmony(adata.obsm['X_pca'], adata.obs, 'batch')
adata.obsm['X_pca_harmony'] = ho.Z_corr.T

# Compute neighbors using harmony corrected PCs
sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40, use_rep='X_pca_harmony')

# UMAP and clustering
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# Visualization
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
sc.pl.umap(adata, color='batch', ax=axes[0], title='Batch distribution')
sc.pl.umap(adata, color='leiden', ax=axes[1], title='Clustering after integration')
plt.show()
""",

    "cell_type_annotation": """
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt

# Load preprocessed and clustered data
adata = sc.read_h5ad('{data_path}')

# Define marker genes for cell types
marker_genes = {
    'T cells': ['CD3D', 'CD3E', 'CD4', 'CD8A'],
    'B cells': ['CD19', 'CD79A', 'MS4A1'],
    'NK cells': ['GNLY', 'NKG7', 'NCAM1'],
    'Monocytes': ['CD14', 'LYZ', 'CST3'],
    'Dendritic cells': ['FCER1A', 'CST3', 'IL3RA'],
    'Platelets': ['PPBP', 'PF4']
}

# Calculate marker expression
for cell_type, markers in marker_genes.items():
    available_markers = [m for m in markers if m in adata.var_names]
    if available_markers:
        sc.tl.score_genes(adata, available_markers, score_name=f'{cell_type}_score')

# Visualize marker scores
score_cols = [f'{ct}_score' for ct in marker_genes.keys()]
sc.pl.umap(adata, color=score_cols, ncols=3)

# Find marker genes per cluster
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')

# Manual annotation based on markers
cluster_annotations = {}
for cluster in adata.obs['leiden'].unique():
    # Get top genes for this cluster
    cluster_markers = sc.get.rank_genes_groups_df(adata, group=cluster, key='rank_genes_groups')
    top_genes = cluster_markers.head(10)['names'].tolist()
    
    print(f"\\nCluster {cluster} top markers:")
    print(top_genes)
    
    # Auto-suggest based on overlap with known markers
    for cell_type, markers in marker_genes.items():
        if any(m in top_genes for m in markers):
            print(f"  -> Possible {cell_type}")

# Add annotations (example - should be customized based on actual markers)
# cluster_annotations = {
#     '0': 'T cells',
#     '1': 'B cells',
#     '2': 'Monocytes',
#     # ... etc
# }

# Map annotations to cells
# adata.obs['cell_type'] = adata.obs['leiden'].map(cluster_annotations)

# Visualize
# sc.pl.umap(adata, color=['cell_type'], legend_loc='on data')
""",

    "spatial_transcriptomics": """
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt
import squidpy as sq

# Load Visium data
adata = sc.read_visium('{data_path}')
adata.var_names_make_unique()

print(f"Spatial data shape: {adata.shape}")

# Calculate QC metrics
sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False, inplace=True)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)

# Visualize QC metrics on tissue
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
sc.pl.spatial(adata, color=['total_counts', 'n_genes_by_counts', 'pct_counts_mt'], 
               ax=axes, ncols=3)
plt.show()

# Filter
sc.pp.filter_cells(adata, min_counts=500)
sc.pp.filter_genes(adata, min_cells=10)
adata = adata[adata.obs.pct_counts_mt < 20, :]

# Normalize and log transform
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# Find highly variable genes
sc.pp.highly_variable_genes(adata, flavor="seurat", n_top_genes=2000)
adata.raw = adata
adata = adata[:, adata.var.highly_variable]

# Scale
sc.pp.scale(adata, max_value=10)

# PCA
sc.tl.pca(adata, svd_solver='arpack')

# Compute spatial neighbors
sq.gr.spatial_neighbors(adata, coord_type='generic', n_neighs=6)

# Non-spatial clustering for comparison
sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# Spatial clustering
sq.gr.spatial_neighbors(adata, n_rings=1, coord_type='generic', n_neighs=6)
sc.tl.leiden(adata, resolution=0.5, key_added='spatial_leiden')

# Visualization
fig, axes = plt.subplots(1, 2, figsize=(12, 6))
sc.pl.spatial(adata, color='leiden', ax=axes[0], title='Standard clustering')
sc.pl.spatial(adata, color='spatial_leiden', ax=axes[1], title='Spatial clustering')
plt.show()

# Find spatially variable genes
sq.gr.spatial_autocorr(adata, mode='moran')
top_spatial_genes = adata.uns['moranI'].head(10).index.tolist()
sc.pl.spatial(adata, color=top_spatial_genes[:4], ncols=2)
"""
}

# Common bioinformatics operations
BIO_OPERATIONS = {
    "load_data": {
        "h5ad": "adata = sc.read_h5ad('{path}')",
        "10x": "adata = sc.read_10x_mtx('{path}', var_names='gene_symbols', cache=True)",
        "csv": "adata = sc.read_csv('{path}', first_column_names=True)",
        "loom": "adata = sc.read_loom('{path}')"
    },
    
    "quality_control": {
        "calculate_metrics": "sc.pp.calculate_qc_metrics(adata, percent_top=None, log1p=False, inplace=True)",
        "filter_cells": "sc.pp.filter_cells(adata, min_genes={min_genes})",
        "filter_genes": "sc.pp.filter_genes(adata, min_cells={min_cells})",
        "filter_mito": "adata = adata[adata.obs.pct_counts_mt < {max_mito}, :]"
    },
    
    "normalization": {
        "total_count": "sc.pp.normalize_total(adata, target_sum=1e4)",
        "log_transform": "sc.pp.log1p(adata)",
        "scale": "sc.pp.scale(adata, max_value=10)",
        "regress_out": "sc.pp.regress_out(adata, ['total_counts', 'pct_counts_mt'])"
    },
    
    "dimension_reduction": {
        "pca": "sc.tl.pca(adata, svd_solver='arpack')",
        "umap": "sc.tl.umap(adata)",
        "tsne": "sc.tl.tsne(adata, n_pcs={n_pcs})",
        "diffmap": "sc.tl.diffmap(adata)"
    },
    
    "clustering": {
        "leiden": "sc.tl.leiden(adata, resolution={resolution})",
        "louvain": "sc.tl.louvain(adata, resolution={resolution})",
        "kmeans": "from sklearn.cluster import KMeans; kmeans = KMeans(n_clusters={n_clusters})"
    }
}

def get_template(analysis_type: str) -> str:
    """Get a template for a specific analysis type"""
    return ANALYSIS_TEMPLATES.get(analysis_type, ANALYSIS_TEMPLATES["scrna_standard"])

def get_operation(operation: str, method: str) -> str:
    """Get code for a specific operation"""
    if operation in BIO_OPERATIONS and method in BIO_OPERATIONS[operation]:
        return BIO_OPERATIONS[operation][method]
    return None
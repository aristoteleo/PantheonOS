"""Single-cell ATAC-seq downstream analysis and visualization"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from .base import ScATACSeqBase
from ...utils.toolset import tool
from ...utils.log import logger


class ScATACSeqAnalysisToolSet(ScATACSeqBase):
    """Single-cell ATAC-seq downstream analysis toolset"""
    
    def __init__(
        self,
        name: str = "scatac_analysis",
        workspace_path: str = None,
        launch_directory: str = None,
        worker_params: dict = None,
        **kwargs
    ):
        super().__init__(name, workspace_path, launch_directory, worker_params, **kwargs)

    @tool
    def ScATAC_Analysis(self, workflow_type: str, description: str = None):
        """Run a specific scATAC-seq downstream analysis workflow"""
        if workflow_type == "load_cellranger_data":
            return self.run_workflow_load_cellranger_data()
        elif workflow_type == "quality_control":
            return self.run_workflow_quality_control()
        elif workflow_type == "compute_embeddings":
            return self.run_workflow_compute_embeddings()
        elif workflow_type == "find_clusters":
            return self.run_workflow_find_clusters()
        elif workflow_type == "annotate_peaks":
            return self.run_workflow_annotate_peaks()
        elif workflow_type == "differential_accessibility":
            return self.run_workflow_differential_accessibility()
        elif workflow_type == "motif_analysis":
            return self.run_workflow_motif_analysis()
        elif workflow_type == "generate_report":
            return self.run_workflow_generate_report()
        else:
            return "Invalid workflow type"

    def run_workflow_load_cellranger_data(self):
        """Run cellranger data loading workflow"""
        logger.info("Running cellranger data loading workflow")
        load_data_response = f"""
# Load cellranger-atac Outputs into Analysis Format

# Set paths (adjust as needed)
CELLRANGER_PATH="cellranger_output/sample_name"
OUTPUT_DIR="analysis/loaded_data"

# Create output directory
mkdir -p $OUTPUT_DIR

# Check cellranger outputs
echo "Checking cellranger outputs..."
ls -la $CELLRANGER_PATH/outs/

# Load data using Python/scanpy
python3 << EOF
import scanpy as sc
import pandas as pd
import anndata as ad
from pathlib import Path

# Set scanpy settings
sc.settings.verbosity = 3  # verbosity level
sc.settings.set_figure_params(dpi=80, facecolor='white')

# Load cellranger data
adata = sc.read_10x_h5('$CELLRANGER_PATH/outs/filtered_peak_bc_matrix.h5')
adata.var_names_unique()

# Load metadata
summary_df = pd.read_csv('$CELLRANGER_PATH/outs/summary.csv')
print("Summary metrics:", summary_df.iloc[0].to_dict())

# Basic info
print(f"Loaded data shape: {adata.shape}")
print(f"Number of cells: {adata.n_obs}")
print(f"Number of peaks: {adata.n_vars}")

# Save processed data
adata.write('$OUTPUT_DIR/scatac_raw.h5ad')
print("Data saved to $OUTPUT_DIR/scatac_raw.h5ad")
EOF

echo "Data loading completed!"
        """
        return load_data_response

    def run_workflow_quality_control(self):
        """Run quality control workflow"""
        logger.info("Running quality control workflow")
        qc_response = f"""
# scATAC-seq Quality Control Analysis

# Set paths
INPUT_FILE="analysis/loaded_data/scatac_raw.h5ad"
OUTPUT_DIR="analysis/qc"

# Create output directory
mkdir -p $OUTPUT_DIR

# Run QC using Python/scanpy
python3 << EOF
import scanpy as sc
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Load data
adata = sc.read_h5ad('$INPUT_FILE')

# Calculate QC metrics
# Number of peaks per cell
adata.obs['n_peaks'] = np.array((adata.X > 0).sum(axis=1)).flatten()

# Total counts per cell  
adata.obs['total_counts'] = np.array(adata.X.sum(axis=1)).flatten()

# Mitochondrial peaks (if available)
mito_peaks = adata.var_names.str.startswith('chrM')
if mito_peaks.any():
    adata.obs['pct_mito'] = np.array((adata[:, mito_peaks].X.sum(axis=1) / adata.obs['total_counts']).A1) * 100
else:
    adata.obs['pct_mito'] = 0

# Plot QC metrics
fig, axes = plt.subplots(1, 3, figsize=(15, 5))

# Plot 1: Number of peaks per cell
sc.pl.violin(adata, ['n_peaks'], jitter=0.4, multi_panel=True, ax=axes[0])
axes[0].set_title('Peaks per cell')

# Plot 2: Total counts per cell
sc.pl.violin(adata, ['total_counts'], jitter=0.4, multi_panel=True, ax=axes[1]) 
axes[1].set_title('Total counts per cell')

# Plot 3: Mitochondrial percentage
sc.pl.violin(adata, ['pct_mito'], jitter=0.4, multi_panel=True, ax=axes[2])
axes[2].set_title('Mitochondrial %')

plt.tight_layout()
plt.savefig('$OUTPUT_DIR/qc_metrics.pdf')
print("QC plots saved to $OUTPUT_DIR/qc_metrics.pdf")

# Filter cells and peaks
print("Before filtering:")
print(f"Cells: {adata.n_obs}, Peaks: {adata.n_vars}")

# Filter cells: minimum peaks and maximum mitochondrial content
sc.pp.filter_cells(adata, min_genes=200)  # min peaks per cell
adata = adata[adata.obs.pct_mito < 20, :]  # max mito content

# Filter peaks: minimum cells
sc.pp.filter_genes(adata, min_cells=3)  # min cells per peak

print("After filtering:")
print(f"Cells: {adata.n_obs}, Peaks: {adata.n_vars}")

# Save filtered data
adata.write('$OUTPUT_DIR/scatac_filtered.h5ad')
print("Filtered data saved to $OUTPUT_DIR/scatac_filtered.h5ad")
EOF

echo "Quality control completed!"
        """
        return qc_response

    def run_workflow_compute_embeddings(self):
        """Run dimensionality reduction workflow"""
        logger.info("Running dimensionality reduction workflow")
        embeddings_response = f"""
# Compute scATAC-seq Embeddings (LSI, UMAP, t-SNE)

# Set paths
INPUT_FILE="analysis/qc/scatac_filtered.h5ad"
OUTPUT_DIR="analysis/embeddings"

# Create output directory
mkdir -p $OUTPUT_DIR

# Run embeddings using Python/scanpy
python3 << EOF
import scanpy as sc
import numpy as np
import matplotlib.pyplot as plt

# Load filtered data
adata = sc.read_h5ad('$INPUT_FILE')

# Normalize data (binary + TF-IDF)
adata.X = (adata.X > 0).astype(np.float32)  # Binarize
sc.pp.normalize_total(adata, target_sum=1e4)  # Normalize
sc.pp.log1p(adata)  # Log transform

# Compute LSI (Latent Semantic Indexing) - preferred for ATAC-seq
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.decomposition import TruncatedSVD

# TF-IDF transformation
tfidf = TfidfTransformer(norm='l2', sublinear_tf=True)
X_tfidf = tfidf.fit_transform(adata.X)

# LSI (SVD on TF-IDF)
svd = TruncatedSVD(n_components=50, random_state=42)
X_lsi = svd.fit_transform(X_tfidf)

# Store LSI components
adata.obsm['X_lsi'] = X_lsi
adata.uns['lsi'] = {'components': svd.components_, 'explained_variance_ratio': svd.explained_variance_ratio_}

print(f"LSI explained variance ratio (first 10): {svd.explained_variance_ratio_[:10]}")

# Compute UMAP embedding
sc.pp.neighbors(adata, use_rep='X_lsi', n_neighbors=15, n_pcs=50)
sc.tl.umap(adata)

# Compute t-SNE embedding  
sc.tl.tsne(adata, use_rep='X_lsi', n_pcs=50)

# Plot embeddings
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# UMAP plot
sc.pl.umap(adata, ax=axes[0], show=False)
axes[0].set_title('UMAP')

# t-SNE plot
sc.pl.tsne(adata, ax=axes[1], show=False)
axes[1].set_title('t-SNE')

plt.tight_layout()
plt.savefig('$OUTPUT_DIR/embeddings.pdf')
print("Embedding plots saved to $OUTPUT_DIR/embeddings.pdf")

# Save data with embeddings
adata.write('$OUTPUT_DIR/scatac_embeddings.h5ad')
print("Data with embeddings saved to $OUTPUT_DIR/scatac_embeddings.h5ad")
EOF

echo "Embeddings computation completed!"
        """
        return embeddings_response

    def run_workflow_find_clusters(self):
        """Run clustering workflow"""
        logger.info("Running clustering workflow")
        clustering_response = f"""
# Find scATAC-seq Cell Clusters

# Set paths
INPUT_FILE="analysis/embeddings/scatac_embeddings.h5ad"
OUTPUT_DIR="analysis/clustering"

# Create output directory
mkdir -p $OUTPUT_DIR

# Run clustering using Python/scanpy
python3 << EOF
import scanpy as sc
import pandas as pd
import matplotlib.pyplot as plt

# Load data with embeddings
adata = sc.read_h5ad('$INPUT_FILE')

# Leiden clustering (multiple resolutions)
resolutions = [0.1, 0.3, 0.5, 0.7, 1.0]

for res in resolutions:
    sc.tl.leiden(adata, resolution=res, key_added=f'leiden_res_{res}')
    print(f"Resolution {res}: {len(adata.obs[f'leiden_res_{res}'].unique())} clusters")

# Use resolution 0.5 as default
adata.obs['clusters'] = adata.obs['leiden_res_0.5']

# Plot clusters on UMAP
fig, axes = plt.subplots(2, 3, figsize=(18, 12))
axes = axes.flatten()

for i, res in enumerate(resolutions):
    sc.pl.umap(adata, color=f'leiden_res_{res}', ax=axes[i], show=False, 
               title=f'Leiden res={res} ({len(adata.obs[f"leiden_res_{res}"].unique())} clusters)')

# Add QC metrics plot
sc.pl.umap(adata, color='n_peaks', ax=axes[5], show=False, title='Peaks per cell')

plt.tight_layout()
plt.savefig('$OUTPUT_DIR/clustering_resolutions.pdf')
print("Clustering plots saved to $OUTPUT_DIR/clustering_resolutions.pdf")

# Cluster statistics
cluster_stats = adata.obs.groupby('clusters').agg({
    'n_peaks': ['mean', 'std'],
    'total_counts': ['mean', 'std'],
    'pct_mito': ['mean', 'std']
}).round(2)

print("Cluster statistics:")
print(cluster_stats)

# Save cluster assignments
cluster_df = adata.obs[['clusters'] + [f'leiden_res_{res}' for res in resolutions]]
cluster_df.to_csv('$OUTPUT_DIR/cluster_assignments.csv')

# Save data with clusters
adata.write('$OUTPUT_DIR/scatac_clustered.h5ad')
print("Clustered data saved to $OUTPUT_DIR/scatac_clustered.h5ad")
EOF

echo "Clustering completed!"
        """
        return clustering_response

    def run_workflow_annotate_peaks(self):
        """Run peak annotation workflow"""
        logger.info("Running peak annotation workflow")
        annotate_peaks_response = """
# Annotate scATAC-seq Peaks with Genomic Features

# Set paths
PEAKS_FILE="cellranger_output/sample_name/outs/peaks.bed"
OUTPUT_DIR="analysis/peak_annotation"
REFERENCE_GTF="references/human/refdata-cellranger-arc-GRCh38-2024-A/genes/genes.gtf"

# Create output directory
mkdir -p $OUTPUT_DIR

# Basic peak annotation using bedtools
echo "Annotating peaks with genomic features..."

# Extract gene features from GTF
grep -E "(gene|transcript)" $REFERENCE_GTF | awk 'OFS="\\t" {if($3=="gene") print $1,$4-1,$5,$10,$7}' | \\
    sed 's/[";]//g' > $OUTPUT_DIR/genes.bed

# Annotate peaks with genes
bedtools closest -a $PEAKS_FILE -b $OUTPUT_DIR/genes.bed -d > $OUTPUT_DIR/peaks_gene_annotation.bed

# Count annotations by category
echo "Peak annotation summary:"
echo "Total peaks: $(wc -l < $PEAKS_FILE)"
echo "Peaks near genes (< 2kb): $(awk '$NF < 2000' $OUTPUT_DIR/peaks_gene_annotation.bed | wc -l)"
echo "Intergenic peaks (> 10kb): $(awk '$NF > 10000' $OUTPUT_DIR/peaks_gene_annotation.bed | wc -l)"

# More detailed annotation using Python
python3 << EOF
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Load peak-gene annotations
peaks_df = pd.read_csv('$OUTPUT_DIR/peaks_gene_annotation.bed', sep='\t', 
                      names=['chr', 'start', 'end', 'gene_chr', 'gene_start', 'gene_end', 'gene_name', 'strand', 'distance'])

# Categorize peaks
def categorize_peak(distance):
    if distance == 0:
        return 'Gene Body'
    elif distance <= 2000:
        return 'Promoter'
    elif distance <= 10000:
        return 'Distal'
    else:
        return 'Intergenic'

peaks_df['category'] = peaks_df['distance'].apply(categorize_peak)

# Plot annotation categories
category_counts = peaks_df['category'].value_counts()
print("Peak annotation categories:")
print(category_counts)

# Pie chart
plt.figure(figsize=(8, 8))
plt.pie(category_counts.values, labels=category_counts.index, autopct='%1.1f%%')
plt.title('Peak Annotation Categories')
plt.savefig('$OUTPUT_DIR/peak_annotation_pie.pdf')
print("Peak annotation plot saved to $OUTPUT_DIR/peak_annotation_pie.pdf")

# Save detailed annotations
peaks_df.to_csv('$OUTPUT_DIR/peaks_annotated.csv', index=False)
print("Detailed annotations saved to $OUTPUT_DIR/peaks_annotated.csv")
EOF

echo "Peak annotation completed!"
        """
        return annotate_peaks_response

    def run_workflow_differential_accessibility(self):
        """Run differential accessibility analysis workflow"""
        logger.info("Running differential accessibility analysis workflow")
        diff_access_response = f"""
# Differential Accessibility Analysis

# Set paths
INPUT_FILE="analysis/clustering/scatac_clustered.h5ad"
OUTPUT_DIR="analysis/differential_accessibility"

# Create output directory
mkdir -p $OUTPUT_DIR

# Run differential analysis using Python/scanpy
python3 << EOF
import scanpy as sc
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Load clustered data
adata = sc.read_h5ad('$INPUT_FILE')

# Find marker peaks for each cluster
sc.tl.rank_genes_groups(adata, 'clusters', method='wilcoxon', n_genes=100)

# Plot top marker peaks
sc.pl.rank_genes_groups(adata, n_genes=10, sharey=False, show=False)
plt.savefig('$OUTPUT_DIR/marker_peaks_heatmap.pdf')
print("Marker peaks heatmap saved to $OUTPUT_DIR/marker_peaks_heatmap.pdf")

# Extract marker peaks results
result = adata.uns['rank_genes_groups']
clusters = result['names'].dtype.names

# Create dataframe of results
marker_peaks = []
for cluster in clusters:
    cluster_peaks = pd.DataFrame({
        'peak': result['names'][cluster],
        'scores': result['scores'][cluster],
        'pvals': result['pvals'][cluster],
        'pvals_adj': result['pvals_adj'][cluster],
        'logfoldchanges': result['logfoldchanges'][cluster],
        'cluster': cluster
    })
    marker_peaks.append(cluster_peaks)

marker_df = pd.concat(marker_peaks, ignore_index=True)

# Filter significant peaks
significant_peaks = marker_df[
    (marker_df['pvals_adj'] < 0.05) & 
    (marker_df['logfoldchanges'] > 0.5)
]

print(f"Total significant marker peaks: {len(significant_peaks)}")
print(f"Peaks per cluster:")
print(significant_peaks.groupby('cluster').size())

# Save results
marker_df.to_csv('$OUTPUT_DIR/marker_peaks_all.csv', index=False)
significant_peaks.to_csv('$OUTPUT_DIR/marker_peaks_significant.csv', index=False)

# Create heatmap of top marker peaks
top_peaks = []
for cluster in clusters:
    cluster_top = significant_peaks[significant_peaks['cluster'] == cluster].head(5)
    top_peaks.extend(cluster_top['peak'].tolist())

if len(top_peaks) > 0:
    sc.pl.heatmap(adata, var_names=top_peaks, groupby='clusters', show=False)
    plt.savefig('$OUTPUT_DIR/top_marker_peaks_heatmap.pdf')
    print("Top marker peaks heatmap saved to $OUTPUT_DIR/top_marker_peaks_heatmap.pdf")

print("Differential accessibility analysis completed!")
EOF
        """
        return diff_access_response

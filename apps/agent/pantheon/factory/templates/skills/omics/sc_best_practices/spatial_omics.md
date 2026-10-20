---
id: sc_bp_spatial_omics
name: "SC Best Practices: Spatial Omics"
description: |
  Spatial transcriptomics analysis including neighborhood analysis,
  spatial domains, spatially variable genes, deconvolution, and imputation.
tags: [spatial, transcriptomics, neighborhood, deconvolution, sc-best-practices]
---

# SC Best Practices: Spatial Omics

Analysis of spatial transcriptomics data covering technology platforms,
spatial neighborhood analysis, domain identification, spatially variable
genes, deconvolution of multi-cellular spots, and gene imputation.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Technology Overview

Spatial transcriptomics technologies vary in resolution, throughput, and
gene coverage. The choice of platform determines downstream analysis strategies.

### Sequencing-Based (Multi-Cell Resolution)

| Technology | Resolution | Gene Coverage | Key Feature |
|-----------|-----------|--------------|-------------|
| Visium (10x) | 55 um spots (~1-10 cells) | Whole transcriptome | Most widely used; H&E image paired |
| Visium HD | 2 um bins | Whole transcriptome | Near single-cell resolution |
| Slide-seq | 10 um beads | Whole transcriptome | Higher resolution than Visium |
| Stereo-seq | Sub-cellular (500 nm) | Whole transcriptome | Ultra-high resolution, large FOV |
| HDST | 2 um | Whole transcriptome | High-definition spatial |

### Imaging-Based (Single-Cell / Sub-Cellular Resolution)

| Technology | Resolution | Gene Coverage | Key Feature |
|-----------|-----------|--------------|-------------|
| MERFISH | Sub-cellular | 100-10,000 genes | Multiplexed error-robust FISH |
| seqFISH+ | Sub-cellular | ~10,000 genes | Sequential hybridization |
| CosMx (Nanostring) | Sub-cellular | 1,000+ genes | Commercial platform |
| Xenium (10x) | Sub-cellular | 300-5,000 genes | Commercial, paired with Visium |
| CODEX / PhenoCycler | Sub-cellular | 40-100 proteins | Protein-level spatial |

> [!TIP]
> For initial spatial transcriptomics experiments, **Visium** provides a good
> balance of whole-transcriptome coverage and spatial context. For single-cell
> resolution with targeted panels, **MERFISH/Xenium** are preferred.
> **Stereo-seq** offers the best of both worlds (whole transcriptome at
> sub-cellular resolution) but requires specialized infrastructure.

---

## 2. Data Loading and Preprocessing

### Loading Spatial Data

```python
import scanpy as sc
import squidpy as sq

# Load Visium data
adata_st = sc.read_visium("/path/to/spaceranger/output/")

# Load from H5AD with spatial coordinates
adata_st = sc.read_h5ad("spatial_data.h5ad")

# Verify spatial coordinates are present
print(adata_st.obsm['spatial'].shape)  # (n_spots, 2)

# Standard preprocessing
adata_st.var_names_make_unique()
sc.pp.filter_genes(adata_st, min_cells=3)
adata_st.layers['counts'] = adata_st.X.copy()
sc.pp.normalize_total(adata_st, target_sum=1e4)
sc.pp.log1p(adata_st)
sc.pp.highly_variable_genes(adata_st, n_top_genes=3000)
```

### Visualization with Tissue Image

```python
# Plot gene expression on tissue
sc.pl.spatial(adata_st, color='total_counts', spot_size=1.5)
sc.pl.spatial(adata_st, color=['EPCAM', 'CD3D', 'COL1A1'], spot_size=1.5)
```

---

## 3. Spatial Neighborhood Analysis (Squidpy)

Squidpy provides tools for analyzing spatial organization and
cell-cell interactions in spatial transcriptomics data.

### Building Spatial Connectivity Graphs

```python
import squidpy as sq

# Build spatial neighborhood graph
sq.gr.spatial_neighbors(adata_st, coord_type="generic")

# For Visium: use grid-based connectivity
sq.gr.spatial_neighbors(adata_st, coord_type="grid", n_neighs=6)
```

### Neighborhood Enrichment Analysis

Test whether cell types are co-localized or segregated beyond random expectation:

```python
# Compute neighborhood enrichment (permutation test)
sq.gr.nhood_enrichment(adata_st, cluster_key="cell_type")

# Visualize enrichment z-scores
sq.pl.nhood_enrichment(adata_st, cluster_key="cell_type")
```

The enrichment matrix shows:
- **Positive z-scores**: Cell types co-localize more than expected
- **Negative z-scores**: Cell types are spatially separated

### Co-occurrence Analysis

```python
# Compute co-occurrence probability across spatial distances
sq.gr.co_occurrence(adata_st, cluster_key="cell_type")
sq.pl.co_occurrence(adata_st, cluster_key="cell_type")
```

### Centrality Scores

Quantify the spatial importance of each cell type in the tissue network:

```python
# Compute network centrality metrics
sq.gr.centrality_scores(adata_st, cluster_key="cell_type")
sq.pl.centrality_scores(adata_st, cluster_key="cell_type")
```

### Interaction Matrix and Ligand-Receptor Analysis

```python
# Compute interaction matrix
sq.gr.interaction_matrix(adata_st, cluster_key="cell_type")
sq.pl.interaction_matrix(adata_st, cluster_key="cell_type")

# Ligand-receptor interaction analysis
sq.gr.ligrec(
    adata_st,
    cluster_key="cell_type",
    n_perms=1000,
    use_raw=False,
)
sq.pl.ligrec(adata_st, cluster_key="cell_type", pvalue_threshold=0.01)
```

---

## 4. Spatial Domain Identification

Spatial domains are regions of tissue with coherent transcriptomic profiles,
incorporating both gene expression and spatial context.

### Graph-Based Methods

| Method | Approach | Key Feature |
|--------|----------|-------------|
| SpaGCN | Graph convolutional network | Integrates histology image features |
| STAGATE | Graph attention autoencoder | Adaptive spatial attention mechanism |
| BayesSpace | Bayesian clustering | Spatial smoothing prior; statistically principled |
| BANKSY | Spatial + neighborhood features | Augments expression with neighbor mean/gradient |

### SpaGCN Workflow

```python
import SpaGCN as spg
import numpy as np

# Set spatial coordinates
x_array = adata_st.obsm['spatial'][:, 0]
y_array = adata_st.obsm['spatial'][:, 1]

# Calculate adjacency matrix with histology
adj = spg.calculate_adj_matrix(
    x=x_array, y=y_array,
    histology=True, beta=49, alpha=1,
    img=img, x_pixel=x_pixel, y_pixel=y_pixel
)

# Find optimal resolution
l = spg.search_l(0.5, adj, init=0.01)
n_clusters = 7  # Expected number of domains
res = spg.search_res(adata_st, adj, l, n_clusters, start=0.7, step=0.1)

# Run SpaGCN
clf = spg.SpaGCN()
clf.set_l(l)
clf.train(adata_st, adj, init_spa=True, init="louvain", res=res, tol=5e-3)
pred, prob = clf.predict()
adata_st.obs['spagcn_domain'] = pred
```

### STAGATE Workflow

```python
import STAGATE_pyG as STAGATE

# Construct spatial graph
STAGATE.Cal_Spatial_Net(adata_st, rad_cutoff=150)

# Train STAGATE
adata_st = STAGATE.train_STAGATE(
    adata_st,
    alpha=0,
    n_epochs=1000,
    random_seed=42,
)

# Cluster on STAGATE embeddings
sc.pp.neighbors(adata_st, use_rep='STAGATE')
sc.tl.leiden(adata_st, resolution=0.5, key_added='stagate_domain')
```

---

## 5. Spatially Variable Genes

Identify genes whose expression varies as a function of spatial location
(beyond what would be expected from random arrangement).

### Moran's I (Squidpy)

Moran's I tests for spatial autocorrelation -- whether nearby spots have
more similar expression than expected by chance:

```python
# Compute spatial autocorrelation (Moran's I)
sq.gr.spatial_autocorr(adata_st, mode="moran")

# Results stored in adata_st.uns['moranI']
# Sort by Moran's I statistic
moranI_results = adata_st.uns['moranI'].sort_values('I', ascending=False)
print(moranI_results.head(20))

# Geary's C (alternative to Moran's I)
sq.gr.spatial_autocorr(adata_st, mode="geary")
```

### SpatialDE

SpatialDE models spatially variable genes using Gaussian process regression:

```python
import NaiveDE
import SpatialDE

# Prepare data
counts = adata_st.to_df()
coords = pd.DataFrame(adata_st.obsm['spatial'], columns=['x', 'y'],
                       index=adata_st.obs_names)

# Stabilize variance
norm_expr = NaiveDE.stabilize(counts.T).T

# Run SpatialDE
results = SpatialDE.run(coords, norm_expr)
results = results.sort_values('qval')

# Significant spatially variable genes
svg = results[results['qval'] < 0.05]
```

### SPARK

SPARK provides a statistical test based on generalized linear spatial models:

```python
# SPARK is R-based; use via rpy2 or standalone R script
# Results provide p-values for spatial expression patterns
```

> [!TIP]
> **Moran's I** (via Squidpy) is the fastest and most accessible option for
> initial SVG identification. Use **SpatialDE** for more rigorous statistical
> testing and pattern decomposition. All methods require a spatial connectivity
> graph to be built first.

---

## 6. Spatial Deconvolution

Multi-cellular spatial technologies (Visium, Slide-seq) measure bulk
expression from spots containing multiple cells. Spatial deconvolution
estimates cell-type proportions per spot using a single-cell reference.

### Method Comparison

| Method | Approach | GPU | Key Feature |
|--------|----------|-----|-------------|
| **Cell2location** | Hierarchical Bayesian | Yes (recommended) | Best overall performance; models technical variation |
| Stereoscope | Probabilistic (NB model) | Yes | Scalable to large datasets |
| RCTD | Robust regression | No | Fast, R-based |
| SPOTlight | NMF + NNLS | No | Simple, fast |
| DestDE | Deep learning | Yes | End-to-end learning |
| Tangram | Optimal transport | Optional | Also does gene imputation |

> [!TIP]
> **Cell2location** is the recommended method for spatial deconvolution based
> on benchmarking studies. It provides well-calibrated uncertainty estimates
> and handles batch effects between reference and spatial data.

### Cell2location Workflow

Cell2location uses a two-step approach:
1. **Reference model**: Learn cell-type gene expression signatures from scRNA-seq
2. **Spatial mapping model**: Decompose spatial spots using learned signatures

```python
import cell2location
import scvi

# ----- Step 1: Reference Model -----
# Prepare single-cell reference
adata_ref = sc.read_h5ad("reference_atlas.h5ad")

# Ensure raw counts
if 'counts' in adata_ref.layers:
    adata_ref.X = adata_ref.layers['counts'].copy()

# Filter genes for deconvolution
from cell2location.utils.filtering import filter_genes
selected = filter_genes(adata_ref, cell_count_cutoff=5,
                         cell_percentage_cutoff2=0.03,
                         nonz_mean_cutoff=1.12)
adata_ref = adata_ref[:, selected].copy()

# Setup and train reference model
cell2location.models.RegressionModel.setup_anndata(
    adata=adata_ref,
    batch_key='sample',
    labels_key='cell_type',
)
mod_ref = cell2location.models.RegressionModel(adata_ref)
mod_ref.train(max_epochs=250, use_gpu=True)

# Export estimated reference signatures
adata_ref = mod_ref.export_posterior(
    adata_ref,
    sample_kwargs={'num_samples': 1000, 'batch_size': 2500, 'use_gpu': True}
)
inf_aver = adata_ref.varm['means_per_cluster_mu_fg']

# ----- Step 2: Spatial Mapping Model -----
# Prepare spatial data
adata_st = sc.read_visium("/path/to/spaceranger/output/")
adata_st.X = adata_st.layers['counts'].copy()

# Intersect genes
intersect_genes = adata_ref.var_names.intersection(adata_st.var_names)
adata_st = adata_st[:, intersect_genes].copy()

# Setup and train spatial model
cell2location.models.Cell2location.setup_anndata(
    adata=adata_st,
    batch_key='sample',
)

# N_cells_per_location: estimate from histology (e.g., ~5 for Visium)
# detection_alpha: higher = more regularization
mod_spatial = cell2location.models.Cell2location(
    adata_st,
    cell_state_df=inf_aver,
    N_cells_per_location=5,       # Estimate from tissue histology
    detection_alpha=20,
)
mod_spatial.train(max_epochs=30000, batch_size=None, train_size=1, use_gpu=True)

# Export results
adata_st = mod_spatial.export_posterior(
    adata_st,
    sample_kwargs={'num_samples': 1000, 'batch_size': adata_st.n_obs, 'use_gpu': True}
)

# Cell-type abundance estimates stored in adata_st.obsm['q05_cell_abundance_w_sf']
# Visualize
cell2location.utils.plot_spatial(
    adata_st,
    color=adata_st.obsm['q05_cell_abundance_w_sf'].columns,
    spot_size=1.5,
)
```

> [!WARNING]
> The `N_cells_per_location` parameter significantly affects results.
> Estimate it from histology images (count cells per spot) or use tissue-type
> defaults: ~5 for Visium cortex, ~8-15 for dense tissues like liver.
> Setting this too low forces the model to under-estimate rare cell types.

> [!WARNING]
> Cell2location requires a GPU for reasonable training times. CPU-only
> training of 30,000 epochs can take many hours even for small datasets.

---

## 7. Gene Imputation

Imaging-based spatial technologies (MERFISH, Xenium) measure only a targeted
gene panel. Gene imputation predicts unmeasured gene expression at spatial
locations using a scRNA-seq reference.

### Method Comparison

| Method | Approach | Key Feature |
|--------|----------|-------------|
| **Tangram** | Optimal transport | Top performer in benchmarks; maps cells to space |
| gimVI | Variational inference | Joint generative model for spatial + scRNA |
| SpaGE | Domain adaptation | Principal vectors for cross-domain mapping |
| novoSpaRc | Optimal transport | Structural correspondence-based |

> [!TIP]
> **Tangram** consistently outperforms other imputation methods in benchmarks
> and is recommended as the default choice for gene imputation.

### Tangram Workflow

```python
import tangram as tg

# Prepare data
adata_sc = sc.read_h5ad("reference_scrna.h5ad")
adata_st = sc.read_h5ad("spatial_data.h5ad")

# Select marker genes for mapping (overlap between spatial panel and reference)
markers = list(set(adata_st.var_names) & set(adata_sc.var_names))
# Or use DE-derived markers:
sc.tl.rank_genes_groups(adata_sc, groupby='cell_type', method='wilcoxon')
markers_df = sc.get.rank_genes_groups_df(adata_sc, group=None)
markers = markers_df[markers_df['pvals_adj'] < 0.01]['names'].unique().tolist()
markers = [m for m in markers if m in adata_st.var_names]

# Preprocess for Tangram
tg.pp_adatas(adata_sc, adata_st, genes=markers)

# Map cells to spatial locations
ad_map = tg.map_cells_to_space(
    adata_sc, adata_st,
    mode="cells",                       # Map individual cells
    density_prior="rna_count_based",    # Weight by RNA content
    num_epochs=500,
    device="cuda:0",                    # Use GPU if available
)

# Project unmeasured genes to spatial locations
ad_ge = tg.project_genes(adata_map=ad_map, adata_sc=adata_sc)

# ad_ge now contains predicted expression for ALL genes in the reference
# at each spatial location
print(ad_ge.shape)  # (n_spatial_spots, n_genes_in_reference)

# Validate: compare predicted vs. measured for genes in the spatial panel
tg.plot_genes_sc(ad_ge, genes=["GENE1", "GENE2"], perc=0.02)
```

### Tangram Mapping Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `"cells"` | Map individual cells to spots | Default; best for most applications |
| `"clusters"` | Map cell-type centroids | Faster; when cell-level resolution not needed |
| `"constrained"` | Map with known cell counts | When cell counts per spot are available |

---

## 8. Spatial Communication Analysis

### CellChat for Spatial Data

```python
# Spatial ligand-receptor analysis with Squidpy
sq.gr.ligrec(
    adata_st,
    cluster_key="cell_type",
    n_perms=1000,
    use_raw=False,
)

# Visualize significant interactions
sq.pl.ligrec(
    adata_st,
    cluster_key="cell_type",
    pvalue_threshold=0.01,
    means_range=(0.5, None),
)
```

---

## Best Practices Summary

1. **Match analysis to resolution**: Multi-cellular technologies (Visium) require deconvolution; single-cell technologies (MERFISH, Xenium) may need gene imputation.
2. **Build spatial graphs carefully**: The choice of coordinate type (`"generic"` vs. `"grid"`) and neighbor count affects all downstream spatial analyses.
3. **Validate deconvolution with histology**: Compare estimated cell-type distributions with H&E-stained tissue images when available.
4. **Set `N_cells_per_location` from data**: Use histology image cell counts, not arbitrary defaults, for Cell2location.
5. **Use Moran's I for quick SVG screening**: Follow up with SpatialDE for statistically rigorous results.
6. **Validate imputed genes**: Always compare predicted expression with measured expression for genes in the spatial panel before trusting imputed values.
7. **Consider spatial domain methods over standard clustering**: Standard Leiden/Louvain clustering ignores spatial context. SpaGCN, STAGATE, and BayesSpace incorporate spatial information for more biologically meaningful domains.
8. **Store spatial coordinates carefully**: Ensure `adata.obsm['spatial']` contains (x, y) coordinates in consistent units across analyses.

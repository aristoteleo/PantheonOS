---
id: sc_best_practices_preprocessing
name: "SC Best Practices: Preprocessing"
description: |
  Preprocessing workflow for single-cell RNA-seq based on SC Best Practices
  (Chapters 6-9). Covers quality control with MAD-based adaptive thresholds,
  normalization strategies, feature selection, and dimensionality reduction.
tags: [preprocessing, qc, normalization, hvg, pca, umap, scanpy, best-practices]
---

# SC Best Practices: Preprocessing (Chapters 6-9)

Comprehensive preprocessing workflow for single-cell RNA-seq data based on the
[SC Best Practices](https://www.sc-best-practices.org) book, covering quality
control, normalization, feature selection, and dimensionality reduction.

---

## Chapter 6: Quality Control

Quality control removes low-quality cells and uninformative genes before
downstream analysis. The goal is to retain true biological signal while
discarding technical artifacts such as dead cells, empty droplets, and doublets.

### 6.1 QC Metrics

Three primary per-cell QC metrics form the foundation of quality filtering:

| Metric | Stored In | Meaning |
|--------|-----------|---------|
| `total_counts` | `adata.obs` | Total UMI counts per cell (library size) |
| `n_genes_by_counts` | `adata.obs` | Number of genes with at least one count |
| `pct_counts_mt` | `adata.obs` | Percentage of counts from mitochondrial genes |

```python
import scanpy as sc
import numpy as np

# --- Gene annotation ---
# Mitochondrial genes
adata.var["mt"] = adata.var_names.str.startswith("MT-")    # Human
# adata.var["mt"] = adata.var_names.str.startswith("mt-")  # Mouse

# Ribosomal genes (optional)
adata.var["ribo"] = adata.var_names.str.startswith(("RPS", "RPL"))  # Human

# Hemoglobin genes (optional, useful for blood/bone marrow samples)
adata.var["hb"] = adata.var_names.str.match("^HB[^P]")  # Human

# Compute QC metrics
sc.pp.calculate_qc_metrics(
    adata,
    qc_vars=["mt", "ribo", "hb"],
    inplace=True,
    log1p=True,
)
```

> [!NOTE]
> **Species-specific prefixes**:
> - Human: `MT-` (mitochondrial), `RPS`/`RPL` (ribosomal), `HB` (hemoglobin)
> - Mouse: `mt-` (mitochondrial), `Rps`/`Rpl` (ribosomal), `Hb` (hemoglobin)
>
> Always verify the prefix convention in your dataset with
> `adata.var_names[adata.var_names.str.startswith("MT-")]`.

### 6.2 Adaptive Thresholding with MAD

Fixed thresholds are fragile across tissues and protocols. Instead, use
**Median Absolute Deviation (MAD)**-based adaptive thresholds that adjust to
each dataset's distribution.

```python
import numpy as np
from scipy.stats import median_abs_deviation

def is_outlier(adata, metric: str, nmads: int):
    M = adata.obs[metric]
    outlier = (M < np.median(M) - nmads * median_abs_deviation(M)) | (
        np.median(M) + nmads * median_abs_deviation(M) < M
    )
    return outlier

adata.obs["outlier"] = (
    is_outlier(adata, "log1p_total_counts", 5)
    | is_outlier(adata, "log1p_n_genes_by_counts", 5)
    | is_outlier(adata, "pct_counts_in_top_20_genes", 5)
)
adata.obs["mt_outlier"] = is_outlier(adata, "pct_counts_mt", 3) | (
    adata.obs["pct_counts_mt"] > 8
)
adata = adata[(~adata.obs.outlier) & (~adata.obs.mt_outlier)].copy()
```

> [!IMPORTANT]
> **Threshold rationale**:
> - **5 MADs** for general metrics (`log1p_total_counts`, `log1p_n_genes_by_counts`,
>   `pct_counts_in_top_20_genes`) -- permissive to retain biological diversity.
> - **3 MADs** for mitochondrial percentage combined with an **8% hard cutoff** --
>   stricter because high mitochondrial content reliably indicates dying cells.
> - The hard cutoff at 8% prevents retaining cells that pass MAD filtering but
>   still have biologically implausible mitochondrial fractions.

### 6.3 Ambient RNA Correction with SoupX

Ambient RNA from lysed cells contaminates droplet-based scRNA-seq data. SoupX
estimates and removes this contamination.

**Requirements**: Both raw (including empty droplets) and filtered count matrices
from Cell Ranger, plus preliminary clustering.

```python
# SoupX requires R -- use rpy2 interface
import rpy2.robjects as robjects
from rpy2.robjects import pandas2ri
import anndata2ri

robjects.default_converter += pandas2ri.converter + anndata2ri.converter
```

```r
%%R -i cellranger_dir -o soupx_out_dir -o contamination_rate

library(SoupX)
library(Seurat)

# Load raw + filtered matrices
sc <- load10X(cellranger_dir)

# Quick clustering if not already present
if (is.null(sc$metaData$clusters)) {
    srat <- CreateSeuratObject(sc$toc) %>%
        NormalizeData(verbose = FALSE) %>%
        FindVariableFeatures(verbose = FALSE) %>%
        ScaleData(verbose = FALSE) %>%
        RunPCA(verbose = FALSE) %>%
        FindNeighbors(verbose = FALSE) %>%
        FindClusters(verbose = FALSE)
    sc <- setClusters(sc, setNames(
        as.character(srat@meta.data$seurat_clusters), colnames(srat)
    ))
}

# Estimate and correct contamination
sc <- autoEstCont(sc)
contamination_rate <- sc$fit$rhoEst
out <- adjustCounts(sc)

# Write corrected counts to disk
soupx_out_dir <- "soupx_corrected"
DropletUtils::write10xCounts(soupx_out_dir, out, version = "3")
```

```python
# Load corrected counts back into AnnData
if soupx_out_dir != "NULL":
    adata_corr = sc.read_10x_mtx(soupx_out_dir, var_names="gene_symbols")
    adata.layers["soupX_counts"] = adata_corr.X.copy()
    adata.X = adata.layers["soupX_counts"].copy()
    print(f"SoupX contamination rate: {contamination_rate:.2%}")
```

> [!TIP]
> If only the filtered matrix is available (no raw matrix), use **DecontX** from
> the `celda` R package as a fallback. It does not require empty droplet profiles.

### 6.4 Doublet Detection with scDblFinder

Doublets are droplets containing two or more cells. scDblFinder has the highest
accuracy among doublet detection methods in published benchmarks.

```python
# scDblFinder runs in R via Bioconductor
```

```r
%%R -i adata -o doublet_score -o doublet_class

library(scDblFinder)
library(SingleCellExperiment)

sce <- scDblFinder(adata)
doublet_score <- sce$scDblFinder.score
doublet_class <- sce$scDblFinder.class
```

```python
# Apply doublet labels to AnnData
adata.obs["scDblFinder_score"] = doublet_score
adata.obs["scDblFinder_class"] = doublet_class

print(f"Predicted doublets: {(adata.obs['scDblFinder_class'] == 'doublet').sum()}")
print(f"Doublet rate: {(adata.obs['scDblFinder_class'] == 'doublet').mean():.1%}")

# Filter doublets
adata = adata[adata.obs["scDblFinder_class"] == "singlet"].copy()
```

> [!WARNING]
> Run doublet detection **before** filtering cells by QC metrics. Doublet
> detectors need the full cell population (including high-count cells) for
> accurate simulation-based identification.

### 6.5 Gene Filtering

After cell-level QC, remove genes detected in very few cells:

```python
# Keep genes expressed in at least 20 cells
sc.pp.filter_genes(adata, min_cells=20)
print(f"Genes after filtering: {adata.n_vars}")
```

> [!NOTE]
> The threshold `min_cells=20` is more stringent than the common default of 3.
> This reduces noise from sporadically detected genes that add dimensionality
> without contributing reliable biological signal.

### 6.6 QC Visualization

```python
import matplotlib.pyplot as plt

# Violin plots of QC metrics
sc.pl.violin(
    adata,
    ["n_genes_by_counts", "total_counts", "pct_counts_mt"],
    jitter=0.4,
    multi_panel=True,
    show=False,
)
plt.savefig("qc_violin.png", dpi=150, bbox_inches="tight")
plt.show()

# Scatter: genes vs counts colored by MT%
sc.pl.scatter(
    adata, x="total_counts", y="n_genes_by_counts", color="pct_counts_mt",
    show=False,
)
plt.savefig("qc_scatter.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## Chapter 7: Normalization

Normalization removes technical variation (sequencing depth differences) so that
gene expression levels are comparable across cells. The choice of method depends
on the downstream analysis task.

### 7.1 Shifted Logarithm (General Purpose)

The standard normalization for most single-cell workflows. Scales each cell to a
common library size, then applies log1p transformation.

```python
# Store raw counts before normalization
adata.layers["counts"] = adata.X.copy()

# Normalize to median library size, then log-transform
sc.pp.normalize_total(adata, target_sum=None)  # None = median total counts
sc.pp.log1p(adata)
```

> [!NOTE]
> Setting `target_sum=None` normalizes to the median total counts across cells,
> which is generally preferred over a fixed value like 10,000. This avoids
> introducing artificial variance for datasets where median library size differs
> substantially from 10,000.

### 7.2 Scran Pooling-Based Size Factors

Scran estimates cell-specific size factors by pooling cells and deconvolving
pool-based estimates. Produces more accurate size factors than simple library
size normalization, especially when cell populations differ in RNA content.

**Best for**: Workflows that feed into batch correction or integration.

```python
import rpy2.robjects as robjects
from rpy2.robjects import pandas2ri
import anndata2ri

robjects.default_converter += pandas2ri.converter + anndata2ri.converter
```

```r
%%R -i adata -o size_factors

library(scran)
library(scuttle)
library(SingleCellExperiment)

sce <- adata
clusters <- quickCluster(sce)
sce <- computeSumFactors(sce, clusters = clusters)
size_factors <- sizeFactors(sce)
```

```python
# Apply scran size factors
adata.obs["size_factors"] = size_factors
adata.X /= adata.obs["size_factors"].values[:, None]
sc.pp.log1p(adata)
adata.layers["scran_normalization"] = adata.X.copy()
```

### 7.3 Analytic Pearson Residuals

Pearson residuals model counts with a negative binomial distribution and return
residuals that are approximately standard normal for non-variable genes. This
approach avoids the pseudo-count bias inherent in log-transformation.

**Best for**: HVG selection, detection of rare cell types, variance-stabilized
representations.

```python
import scanpy.experimental as sce

# Compute Pearson residuals (operates on raw counts)
sce.pp.normalize_pearson_residuals(adata)
```

> [!TIP]
> **Choosing a normalization method**:
>
> | Method | When to Use | Downstream Task |
> |--------|-------------|-----------------|
> | Shifted logarithm | Default, general-purpose | Clustering, DE, visualization |
> | Scran pooling | Heterogeneous RNA content, batch correction | Integration, cross-sample comparisons |
> | Pearson residuals | HVG selection, rare cell types | Feature selection, specialized dim. reduction |
>
> You can store multiple normalizations in `adata.layers` and switch between
> them as needed for different analysis steps.

---

## Chapter 8: Feature Selection

Feature selection identifies genes with high biological variability across cells
(highly variable genes, HVGs) while discarding genes dominated by technical
noise. This reduces dimensionality and improves signal-to-noise ratio.

### 8.1 Highly Variable Genes (Standard)

The standard Scanpy approach selects genes with high variance relative to their
mean expression using the Seurat v3 method.

```python
# Standard HVG selection on log-normalized data
sc.pp.highly_variable_genes(
    adata,
    n_top_genes=2000,
    flavor="seurat_v3",    # Operates on raw counts internally
    batch_key=None,        # Set to batch column for batch-aware selection
    layer="counts",        # Use raw count layer for seurat_v3
)

# Visualize HVG selection
sc.pl.highly_variable_genes(adata, show=False)
plt.savefig("hvg_selection.png", dpi=150, bbox_inches="tight")
plt.show()

print(f"Selected {adata.var['highly_variable'].sum()} highly variable genes")
```

> [!IMPORTANT]
> **Batch-aware HVG selection**: When working with multiple samples or batches,
> set `batch_key` to ensure HVGs are selected based on within-batch variability
> rather than batch-driven differences:
> ```python
> sc.pp.highly_variable_genes(
>     adata,
>     n_top_genes=2000,
>     flavor="seurat_v3",
>     batch_key="sample",
>     layer="counts",
> )
> ```

### 8.2 Deviance-Based Feature Selection

Deviance-based selection works directly on raw counts using a binomial deviance
statistic. It avoids the pseudo-count and normalization biases that affect
variance-based methods.

```python
from scvi.model import SCVI

# Deviance-based feature selection using scvi-tools
# Operates on raw counts -- no normalization needed
SCVI.setup_anndata(adata, layer="counts")

# Alternatively, use the standalone deviance function
from scvi.data import poisson_gene_selection

poisson_gene_selection(
    adata,
    n_top_genes=4000,
    layer="counts",
)
```

> [!TIP]
> Deviance-based selection tends to select **more informative genes** than
> variance-based methods because it is not confounded by mean-expression level.
> Select the **top 4000 genes** for deviance-based methods (compared to the
> typical 2000 for variance-based) because the ranking is more stable.

---

## Chapter 9: Dimensionality Reduction

Dimensionality reduction compresses gene expression into a smaller number of
components that capture the dominant axes of variation. This is essential for
neighborhood computation, clustering, and visualization.

### 9.1 PCA

Principal Component Analysis captures the top axes of linear variation.

```python
# Subset to HVGs for PCA
adata_hvg = adata[:, adata.var["highly_variable"]].copy()

# Run PCA
sc.tl.pca(adata_hvg, n_comps=50, svd_solver="arpack")

# Inspect variance explained to choose number of PCs
sc.pl.pca_variance_ratio(adata_hvg, n_pcs=50, log=True, show=False)
plt.savefig("pca_variance_ratio.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!NOTE]
> **Choosing the number of PCs**: Typically 10-50 PCs are used. Look for an
> "elbow" in the variance ratio plot where additional PCs contribute diminishing
> returns. In practice, using 30-50 PCs is robust for most datasets -- including
> a few extra PCs rarely hurts, while using too few can lose real biological
> signal.

### 9.2 Neighborhood Graph

The k-nearest neighbor graph encodes cell-cell similarities and serves as the
input to clustering algorithms and non-linear embeddings.

```python
# Compute neighborhood graph from PCA
sc.pp.neighbors(adata_hvg, n_neighbors=15, n_pcs=30)
```

### 9.3 UMAP

UMAP produces 2D embeddings for visualization. It preserves local structure
well but global distances are **not** meaningful.

```python
sc.tl.umap(adata_hvg)

sc.pl.umap(adata_hvg, color=["leiden", "total_counts", "pct_counts_mt"], show=False)
plt.savefig("umap.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!CAUTION]
> **UMAP is for visualization only.** Do not interpret distances between distant
> clusters as biologically meaningful. Cluster-to-cluster distances, relative
> positions, and the size/density of clusters on a UMAP can change dramatically
> with different random seeds or hyperparameters. Never run differential
> expression or trajectory inference on UMAP coordinates.

### 9.4 t-SNE

t-SNE is an alternative non-linear embedding that is robust and largely
equivalent to UMAP for visualization purposes (Kobak & Berens, 2019).

```python
sc.tl.tsne(adata_hvg, n_pcs=30)

sc.pl.tsne(adata_hvg, color=["leiden"], show=False)
plt.savefig("tsne.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!NOTE]
> Per Kobak & Berens (2019), t-SNE and UMAP produce **mostly equivalent**
> visualizations when both are properly tuned. t-SNE tends to produce tighter,
> more separated clusters, while UMAP better preserves continuity. Neither is
> objectively superior for exploration.

---

## Complete Preprocessing Workflow

The following code block combines all steps into a single end-to-end workflow:

```python
import scanpy as sc
import numpy as np
from scipy.stats import median_abs_deviation
import matplotlib.pyplot as plt

# ── 1. Load data ──────────────────────────────────────────────
adata = sc.read_10x_mtx("filtered_feature_bc_matrix/", var_names="gene_symbols")
adata.var_names_make_unique()

# ── 2. Gene annotation ───────────────────────────────────────
adata.var["mt"] = adata.var_names.str.startswith("MT-")
adata.var["ribo"] = adata.var_names.str.startswith(("RPS", "RPL"))
adata.var["hb"] = adata.var_names.str.match("^HB[^P]")

# ── 3. QC metrics ────────────────────────────────────────────
sc.pp.calculate_qc_metrics(
    adata, qc_vars=["mt", "ribo", "hb"], inplace=True, log1p=True
)

# ── 4. MAD-based outlier detection ───────────────────────────
def is_outlier(adata, metric: str, nmads: int):
    M = adata.obs[metric]
    outlier = (M < np.median(M) - nmads * median_abs_deviation(M)) | (
        np.median(M) + nmads * median_abs_deviation(M) < M
    )
    return outlier

adata.obs["outlier"] = (
    is_outlier(adata, "log1p_total_counts", 5)
    | is_outlier(adata, "log1p_n_genes_by_counts", 5)
    | is_outlier(adata, "pct_counts_in_top_20_genes", 5)
)
adata.obs["mt_outlier"] = is_outlier(adata, "pct_counts_mt", 3) | (
    adata.obs["pct_counts_mt"] > 8
)

print(f"Cells before QC: {adata.n_obs}")
adata = adata[(~adata.obs.outlier) & (~adata.obs.mt_outlier)].copy()
print(f"Cells after QC:  {adata.n_obs}")

# ── 5. Ambient RNA correction (if raw matrix available) ──────
# See Section 6.3 for SoupX workflow

# ── 6. Doublet detection ─────────────────────────────────────
# See Section 6.4 for scDblFinder workflow

# ── 7. Gene filtering ────────────────────────────────────────
sc.pp.filter_genes(adata, min_cells=20)

# ── 8. Normalization (shifted logarithm) ─────────────────────
adata.layers["counts"] = adata.X.copy()
sc.pp.normalize_total(adata)
sc.pp.log1p(adata)

# ── 9. Feature selection (HVGs) ──────────────────────────────
sc.pp.highly_variable_genes(
    adata, n_top_genes=2000, flavor="seurat_v3", layer="counts"
)

# ── 10. Dimensionality reduction ─────────────────────────────
sc.tl.pca(adata, n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)
sc.tl.umap(adata)

# ── 11. Visualization ────────────────────────────────────────
sc.pl.umap(adata, color=["total_counts", "n_genes_by_counts", "pct_counts_mt"],
           show=False)
plt.savefig("preprocessing_overview.png", dpi=150, bbox_inches="tight")
plt.show()

# ── 12. Save preprocessed object ─────────────────────────────
adata.write("preprocessed.h5ad")
print("Preprocessing complete.")
```

---

## Best Practices Summary

> [!IMPORTANT]
> **Key decisions and their rationale**:
> 1. **MAD over fixed thresholds** -- Adapts to each dataset; avoids arbitrary cutoffs.
> 2. **SoupX for ambient RNA** -- Requires raw + filtered matrices; use DecontX as fallback.
> 3. **scDblFinder for doublets** -- Highest accuracy in benchmarks (Xi & Li, 2021).
> 4. **min_cells=20 for gene filtering** -- Removes sporadically detected noise genes.
> 5. **Shifted logarithm as default** -- Simple, well-understood, sufficient for most tasks.
> 6. **Pearson residuals for HVG selection** -- More principled alternative when needed.
> 7. **30-50 PCs** -- Robust range; erring on the high side is safer than too few.
> 8. **UMAP for visualization only** -- Never use embedding coordinates for quantitative analysis.

> [!WARNING]
> **Common pitfalls**:
> - Running doublet detection after cell filtering reduces accuracy.
> - Using fixed QC thresholds across tissues/protocols leads to over- or under-filtering.
> - Interpreting UMAP distances as biological distances.
> - Forgetting `batch_key` in HVG selection when data has batch structure.
> - Not storing raw counts in a layer before normalization (loss of count data).

**Source**: [The Single-Cell Best Practices Book](https://www.sc-best-practices.org), Chapters 6-9.

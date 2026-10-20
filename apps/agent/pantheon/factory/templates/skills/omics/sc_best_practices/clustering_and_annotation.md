---
id: sc_bp_clustering_and_annotation
name: "SC Best Practices: Clustering, Annotation & Integration"
description: |
  Best practices for clustering, cell type annotation, and data integration
  in single-cell RNA-seq analysis. Covers Leiden clustering, CellTypist/scArches
  annotation, and batch integration with scVI/BBKNN/Harmony.
  Based on SC Best Practices Chapters 10-12.
tags: [clustering, annotation, integration, scanpy, scvi, celltypist, leiden, batch-correction]
---

# SC Best Practices Part 3: Clustering, Annotation & Integration

Comprehensive guide covering graph-based clustering, automated cell type annotation,
and multi-sample data integration. Based on the
[SC Best Practices](https://www.sc-best-practices.org) textbook (Chapters 10-12).

---

## 1. Clustering (Chapter 10)

Graph-based clustering is the standard approach for grouping cells in scRNA-seq data.
The Leiden algorithm is preferred over Louvain for its guaranteed connected communities.

### Core Workflow

```python
import scanpy as sc
import matplotlib.pyplot as plt

# Step 1: Build KNN graph (prerequisite for clustering)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=30)

# Step 2: Leiden clustering
sc.tl.leiden(adata, resolution=1.0)

# Step 3: Visualize
sc.tl.umap(adata)
sc.pl.umap(adata, color=["leiden"], show=False)
plt.savefig("leiden_clusters.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Resolution Parameter

The resolution parameter controls cluster granularity. Higher values produce more
clusters; lower values produce fewer, broader clusters.

```python
# Explore multiple resolutions
for res in [0.5, 1.0, 1.5, 2.0]:
    sc.tl.leiden(adata, resolution=res, key_added=f"leiden_res{res}")

sc.pl.umap(
    adata,
    color=["leiden_res0.5", "leiden_res1.0", "leiden_res1.5", "leiden_res2.0"],
    ncols=2,
    show=False,
)
plt.savefig("leiden_resolutions.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!TIP]
> Start with resolution 1.0-2.0 for most datasets. Over-clustering is preferred
> over under-clustering because you can always merge clusters later, but splitting
> under-clustered groups requires re-analysis.

### Cluster Validation with Marker Genes

Always validate clusters by checking whether they express biologically meaningful
marker genes. Clusters without distinguishable markers may need merging.

```python
# Find differentially expressed genes per cluster
sc.tl.rank_genes_groups(adata, groupby="leiden", method="wilcoxon", pts=True)
sc.pl.rank_genes_groups(adata, n_genes=10, sharey=False, show=False)
plt.savefig("cluster_markers_ranked.png", dpi=150, bbox_inches="tight")
plt.show()

# Dotplot of top markers per cluster
sc.pl.rank_genes_groups_dotplot(
    adata, n_genes=3, standard_scale="var", show=False
)
plt.savefig("cluster_markers_dotplot.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!WARNING]
> Do not rely solely on UMAP visual separation to judge cluster quality. UMAP is a
> visualization tool, not a clustering method. Clusters that appear merged in UMAP
> may still be transcriptionally distinct, and vice versa. Always validate with
> marker gene analysis.

### Merging Over-Clustered Groups

```python
# Define merge mapping after inspecting markers
merge_map = {
    "0": "CD4 T cells",
    "1": "CD4 T cells",   # Merge with cluster 0
    "2": "CD8 T cells",
    "3": "B cells",
    # ...
}
adata.obs["cell_type_merged"] = adata.obs["leiden"].map(merge_map)
```

---

## 2. Cell Type Annotation (Chapter 11)

Three complementary approaches exist, ranging from manual expert-driven annotation
to fully automated methods.

### Approach Overview

| Approach | Method | Best For | Requirements |
|----------|--------|----------|--------------|
| Manual | Marker genes + DEG | Well-characterized tissues | Domain expertise |
| Automated | CellTypist | Immune/common tissues | Pre-trained model |
| Reference mapping | scArches | Complex datasets with good reference | Reference atlas |

### Approach 1: Manual Marker-Based Annotation

Define known marker genes and validate expression across clusters.

```python
# Define canonical markers (example: PBMC)
marker_genes = {
    "CD4 T": ["CD3D", "CD4", "IL7R"],
    "CD8 T": ["CD3D", "CD8A", "CD8B"],
    "NK": ["GNLY", "NKG7", "KLRD1"],
    "B": ["CD79A", "MS4A1", "CD19"],
    "Mono": ["CD14", "LYZ", "CST3"],
    "DC": ["FCER1A", "CST3", "CLEC10A"],
    "Platelet": ["PPBP", "PF4"],
}

# Dotplot for validation
sc.pl.dotplot(
    adata,
    var_names=marker_genes,
    groupby="leiden",
    standard_scale="var",
    dendrogram=True,
    show=False,
)
plt.savefig("manual_annotation_dotplot.png", dpi=150, bbox_inches="tight")
plt.show()

# Violin plot for detailed expression distributions
sc.pl.stacked_violin(
    adata,
    var_names=["CD3D", "CD79A", "CD14", "NKG7"],
    groupby="leiden",
    show=False,
)
plt.savefig("manual_annotation_violin.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Approach 2: CellTypist (Automated)

CellTypist provides pre-trained models for automated annotation with majority voting
to smooth predictions across clusters.

```python
import celltypist
from celltypist import models

# List available models
models.models_description()

# Download and load model (choose appropriate tissue/organism)
models.download_models(model="Immune_All_High.pkl")
model = celltypist.models.Model.load("Immune_All_High.pkl")

# Annotate (majority_voting smooths predictions over graph neighbors)
predictions = celltypist.annotate(
    adata,
    model=model,
    majority_voting=True,
)

# Transfer predictions to adata
adata.obs["celltypist_cell_type"] = predictions.predicted_labels.majority_voting
adata.obs["celltypist_conf"] = predictions.predicted_labels.confidence_score

# Visualize
sc.pl.umap(adata, color=["celltypist_cell_type"], show=False)
plt.savefig("celltypist_annotation.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!TIP]
> CellTypist works best when the query data matches the training data domain. Use
> `Immune_All_High.pkl` for fine-grained immune cell types or `Immune_All_Low.pkl`
> for coarser annotations. Run `models.models_description()` to browse all available
> models across tissues and organisms.

### Approach 3: scArches (Reference Mapping with Transfer Learning)

scArches maps query data to a reference atlas using weighted KNN transfer.

```python
import scarches as sca

# Train weighted KNN on reference atlas
knn_transformer = sca.utils.knn.weighted_knn_trainer(
    train_adata=ref_adata,
    train_labels="cell_type",
    n_neighbors=15,
)

# Transfer labels to query data
labels, uncertainties = sca.utils.knn.weighted_knn_transfer(
    knn_transformer,
    query_adata,
    ref_adata.obs["cell_type"],
)

# Assign labels; flag uncertain predictions as "Unknown"
query_adata.obs["predicted_cell_type"] = labels
query_adata.obs["prediction_uncertainty"] = uncertainties
query_adata.obs.loc[
    query_adata.obs["prediction_uncertainty"] > 0.2, "predicted_cell_type"
] = "Unknown"
```

### Annotation Best Practices

> [!WARNING]
> Never finalize cell type annotations without marker gene validation. Automated
> methods can misclassify cells, especially for rare or novel cell types not
> represented in the training data. Always cross-check predictions against known
> markers using dotplots or violin plots.

**Key principles:**

1. **Cluster before annotating**: Single-cell data is sparse. Annotating individual
   cells is unreliable. Cluster first, then assign labels per cluster using majority
   voting or consensus across cells.

2. **Majority voting**: When using per-cell annotation tools (CellTypist, scArches),
   aggregate predictions within each cluster. The most frequent label becomes the
   cluster annotation. This handles sparsity-induced noise.

3. **Uncertainty thresholds**: For reference-mapping methods, set a threshold
   (typically uncertainty > 0.2) above which cells are labeled "Unknown" rather than
   forcing an incorrect assignment.

4. **Hierarchical annotation**: Start with broad types (T cell, B cell, Myeloid),
   then sub-cluster to resolve finer subtypes (CD4 naive, CD4 memory, Treg).

```python
# Validate automated annotations against markers
sc.pl.dotplot(
    adata,
    var_names=marker_genes,
    groupby="celltypist_cell_type",
    standard_scale="var",
    show=False,
)
plt.savefig("annotation_validation.png", dpi=150, bbox_inches="tight")
plt.show()
```

---

## 3. Data Integration (Chapter 12)

When combining data from multiple batches, donors, or experiments, batch effects
can obscure biological variation. Integration methods align shared cell populations
while preserving biological differences.

### Decision Framework

> [!TIP]
> Always inspect unintegrated data first. If batches mix well on their own, integration
> may not be needed and can even remove real biological variation. Plot UMAP colored
> by batch to assess whether integration is necessary.

```python
# Check if integration is needed
sc.pl.umap(adata, color=["batch", "cell_type"], ncols=2, show=False)
plt.savefig("pre_integration_check.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Method Selection Guide

| Method | Category | Speed | Input | Best For |
|--------|----------|-------|-------|----------|
| Harmony | Linear embedding | Fast | PCA | Simple batch effects, quick iteration |
| Seurat CCA/RPCA | Linear embedding | Moderate | Counts | Well-characterized batch effects |
| BBKNN | Graph-based | Fastest | KNN graph | Quick integration, small-medium data |
| scVI | Deep learning | Slow | Raw counts | Complex multi-dataset integration |
| scANVI | Deep learning | Slow | Raw counts + labels | Label-aware integration |

### Prerequisite: Batch-Aware Feature Selection

```python
# CRITICAL: Select HVGs accounting for batch structure
sc.pp.highly_variable_genes(
    adata,
    n_top_genes=2000,
    batch_key="batch",   # Identifies genes variable WITHIN batches
    flavor="seurat_v3",
)
adata = adata[:, adata.var["highly_variable"]].copy()
```

> [!WARNING]
> Failing to use `batch_key` in HVG selection can cause batch-specific genes to
> dominate the feature set, undermining downstream integration.

### Method 1: scVI (Recommended for Complex Integration)

scVI uses a variational autoencoder to learn a batch-corrected latent representation.

```python
import scvi

# Setup requires RAW counts (not log-normalized)
scvi.model.SCVI.setup_anndata(
    adata,
    layer="counts",       # Must point to raw integer counts
    batch_key="batch",
)

# Initialize and train model
model = scvi.model.SCVI(adata, n_latent=30, n_layers=2)

# Heuristic for max_epochs based on dataset size
max_epochs = min(round((20000 / adata.n_obs) * 400), 400)
model.train(max_epochs=max_epochs)

# Extract latent representation and use for downstream analysis
adata.obsm["X_scVI"] = model.get_latent_representation()
sc.pp.neighbors(adata, use_rep="X_scVI")
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=1.0)

# Visualize integration result
sc.pl.umap(adata, color=["batch", "leiden"], ncols=2, show=False)
plt.savefig("scvi_integration.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Method 2: scANVI (Label-Aware Integration)

When partial cell type labels are available, scANVI leverages them for improved
integration and simultaneous annotation of unlabeled cells.

```python
# Build scANVI from a pre-trained scVI model
model_scanvi = scvi.model.SCANVI.from_scvi_model(
    model,
    labels_key="cell_type",
    unlabeled_category="unlabelled",
)
model_scanvi.train(max_epochs=10)

# Extract label-aware latent space
adata.obsm["X_scANVI"] = model_scanvi.get_latent_representation()
sc.pp.neighbors(adata, use_rep="X_scANVI")
sc.tl.umap(adata)

# Predict labels for unlabeled cells
adata.obs["scANVI_predictions"] = model_scanvi.predict()

sc.pl.umap(adata, color=["batch", "scANVI_predictions"], ncols=2, show=False)
plt.savefig("scanvi_integration.png", dpi=150, bbox_inches="tight")
plt.show()
```

### Method 3: BBKNN (Fast Graph-Based Integration)

BBKNN modifies the KNN graph to connect cells across batches. It is the fastest
method and works directly on the neighbor graph.

```python
import bbknn

# BBKNN expects log-normalized data with PCA computed
sc.pp.pca(adata)

# Replace sc.pp.neighbors with bbknn
bbknn.bbknn(adata, batch_key="batch", n_pcs=30)

# Proceed with clustering and visualization as normal
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=1.0)

sc.pl.umap(adata, color=["batch", "leiden"], ncols=2, show=False)
plt.savefig("bbknn_integration.png", dpi=150, bbox_inches="tight")
plt.show()
```

> [!TIP]
> BBKNN only outputs a corrected graph (no corrected expression matrix). This is
> sufficient for clustering and UMAP, but if you need corrected expression values
> (e.g., for differential expression), use scVI or Harmony instead.

### Method 4: Harmony (Linear Embedding Correction)

```python
import harmonypy
import scanpy.external as sce

# Run Harmony on PCA embeddings
sc.pp.pca(adata)
sce.pp.harmony_integrate(adata, key="batch")

# Use corrected embedding
sc.pp.neighbors(adata, use_rep="X_pca_harmony")
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=1.0)
```

### Evaluating Integration Quality

Use scIB (single-cell Integration Benchmarking) for quantitative assessment of
integration quality, balancing batch correction against biological conservation.

```python
# Key metrics to evaluate:
# - Batch mixing: ASW_batch, graph_iLISI, kBET
# - Bio conservation: ASW_label, NMI, ARI, cell_type_ASW

import scib

# Compute integration metrics
results = scib.metrics.metrics(
    adata,
    adata_int=adata,          # Integrated adata
    batch_key="batch",
    label_key="cell_type",
    embed="X_scVI",           # Or X_pca_harmony, etc.
    organism="human",
)
print(results)
```

> [!WARNING]
> Over-integration can remove real biological variation. If different batches contain
> different cell types (e.g., tumor vs. normal tissue), forcing them to overlap will
> destroy meaningful signal. Always compare integrated vs. unintegrated results and
> verify that known biological differences are preserved.

### Integration Input Requirements

| Method | Required Input | Normalization |
|--------|---------------|---------------|
| scVI / scANVI | Raw integer counts (`layer="counts"`) | None (model handles it) |
| BBKNN | Log-normalized + PCA | `sc.pp.normalize_total` + `sc.pp.log1p` |
| Harmony | Log-normalized + PCA | `sc.pp.normalize_total` + `sc.pp.log1p` |
| Seurat CCA/RPCA | Raw counts (handled internally) | None |

---

## Recommended Workflow Order

1. **Inspect unintegrated data** -- determine if integration is needed
2. **Batch-aware HVG selection** -- `sc.pp.highly_variable_genes(batch_key="batch")`
3. **Integrate** -- choose method based on dataset complexity
4. **Cluster** -- Leiden on integrated representation
5. **Annotate** -- combine automated + manual marker validation
6. **Validate** -- confirm biological markers, evaluate with scIB

---

## Source

Based on [SC Best Practices](https://www.sc-best-practices.org), Chapters 10-12:
- Chapter 10: Clustering
- Chapter 11: Cell Type Annotation
- Chapter 12: Data Integration / Batch Correction

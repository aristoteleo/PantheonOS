---
id: sc_best_practices_introduction
name: "SC Best Practices: Introduction & Foundations"
description: |
  Single-cell RNA-seq fundamentals covering technology platforms, raw data
  processing pipelines, analysis frameworks, core data structures, file formats,
  and cross-ecosystem interoperability. Based on Chapters 1-5 of SC Best Practices.
tags: [scRNA-seq, preprocessing, anndata, scanpy, seurat, interoperability, best-practices]
---

# SC Best Practices: Introduction & Foundations (Chapters 1-5)

Core concepts for single-cell genomics covering experimental technologies,
raw data processing, analysis ecosystems, data structures, and interoperability.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org) (Chapters 1-5)

---

## 1. scRNA-seq Technologies

Single-cell RNA-seq platforms differ in throughput, sensitivity, and cost.
The choice of technology affects downstream analysis decisions.

| Platform Type | Example | Throughput | Sensitivity | UMI Support | Typical Use |
|---------------|---------|------------|-------------|-------------|-------------|
| Droplet-based | 10x Chromium | High (10k-100k cells) | Moderate | Yes | Large-scale profiling |
| Plate-based | Smart-seq2 | Low (100s cells) | High (full-length) | No | Deep per-cell profiling |
| Combinatorial indexing | sci-RNA-seq | Very high (>100k cells) | Low-Moderate | Yes | Ultra-high throughput |

### Key Distinctions

- **Droplet-based (10x Chromium)**: Cells are encapsulated in oil droplets with barcoded beads. Captures 3' (or 5') end of transcripts. UMI-based quantification reduces amplification bias. Most widely used platform.
- **Plate-based (Smart-seq2)**: Individual cells are sorted into wells. Full-length transcript coverage enables isoform analysis and SNP detection. No UMIs -- reads are used directly for quantification.
- **Combinatorial indexing (sci-RNA-seq)**: Cells are split-pooled across plates with unique barcode combinations. No physical cell isolation required. Scales to millions of cells at low cost per cell.

> [!TIP]
> For most standard single-cell experiments, 10x Chromium is the default choice.
> Use Smart-seq2 when full-length transcript information is critical (e.g., isoform
> analysis, allele-specific expression). Use sci-RNA-seq for atlas-scale projects.

---

## 2. Raw Data Processing

Raw sequencing data (FASTQ) must be aligned, demultiplexed, and quantified
into a count matrix before analysis. The choice of tool depends on the
experimental platform and performance requirements.

### Processing Pipelines

| Tool | Platform | Speed | Notes |
|------|----------|-------|-------|
| CellRanger | 10x Chromium | Moderate | Official 10x pipeline; most validated |
| STARsolo | 10x, Drop-seq | Fast | STAR aligner with single-cell mode |
| Kallisto/bustools | 10x, Drop-seq | Very fast | Pseudoalignment; lightweight |
| Alevin/Salmon | 10x, Drop-seq | Fast | Quasi-mapping; includes UMI deduplication |

### CellRanger Output Structure

```
cellranger_output/
  ├── raw_feature_bc_matrix/       # All detected barcodes (including empty droplets)
  │   ├── barcodes.tsv.gz
  │   ├── features.tsv.gz
  │   └── matrix.mtx.gz
  ├── filtered_feature_bc_matrix/  # Cell-containing barcodes only
  │   ├── barcodes.tsv.gz
  │   ├── features.tsv.gz
  │   └── matrix.mtx.gz
  ├── filtered_feature_bc_matrix.h5
  └── raw_feature_bc_matrix.h5
```

> [!WARNING]
> Always retain the `raw_feature_bc_matrix/` output. It is required for ambient RNA
> correction tools such as SoupX and CellBender. Starting analysis from only the
> filtered matrix limits your ability to assess and correct contamination.

> [!TIP]
> For large datasets or repeated processing, STARsolo and Kallisto/bustools offer
> significantly faster runtimes than CellRanger with comparable accuracy.

---

## 3. Analysis Frameworks

### Python Ecosystem (Scanpy / scverse)

- **Scanpy**: Core single-cell analysis library (preprocessing, clustering, DE, visualization)
- **scverse**: Ecosystem of interoperable packages built around AnnData
  - `scvi-tools` -- probabilistic modeling (integration, imputation, DE)
  - `squidpy` -- spatial transcriptomics
  - `scvelo` -- RNA velocity
  - `muon` -- multi-modal analysis
  - `cellrank` -- fate mapping

### R Ecosystem

- **Seurat**: Most widely used R framework. Comprehensive workflow from raw counts to annotation.
- **Bioconductor / OSCA**: SingleCellExperiment-based ecosystem. Modular packages (scran, scater, batchelor). Follows the Orchestrating Single-Cell Analysis (OSCA) book.

### Choosing a Framework

| Consideration | Scanpy/scverse | Seurat | Bioconductor/OSCA |
|---------------|----------------|--------|--------------------|
| Language | Python | R | R |
| Scalability | Excellent (backed-mode, Dask) | Good | Good |
| Deep learning integration | Native (PyTorch/JAX) | Limited | Limited |
| Statistical rigor | Good | Good | Excellent |
| Community size | Large, growing | Very large | Large |

> [!TIP]
> Most modern computational methods (foundation models, deep learning-based
> integration) are released as Python packages first. If your workflow includes
> these tools, Scanpy/scverse is the most natural choice.

---

## 4. Data Structures

### AnnData (Python)

AnnData is the central data structure for single-cell analysis in the Python ecosystem.

```python
# Read 10x data
import scanpy as sc
adata = sc.read_10x_h5("filtered_feature_bc_matrix.h5")

# Basic AnnData structure
# adata.X - expression matrix (cells x genes, sparse or dense)
# adata.obs - cell metadata (pandas DataFrame, indexed by barcode)
# adata.var - gene metadata (pandas DataFrame, indexed by gene name)
# adata.obsm - embeddings (PCA, UMAP) and other cell-level matrices
# adata.layers - additional matrices (raw counts, normalized, etc.)
# adata.uns - unstructured metadata (colors, parameters, DE results)
```

Key operations:

```python
# Preserve raw counts before normalization
adata.layers['counts'] = adata.X.copy()

# Subset by cells or genes
adata_subset = adata[adata.obs['cell_type'] == 'T cell', :]

# Access embeddings
pca_coords = adata.obsm['X_pca']
umap_coords = adata.obsm['X_umap']
```

### SingleCellExperiment (R / Bioconductor)

```r
# Core structure
library(SingleCellExperiment)
# counts(sce)       - count matrix
# colData(sce)      - cell metadata (equivalent to adata.obs)
# rowData(sce)      - gene metadata (equivalent to adata.var)
# reducedDims(sce)  - embeddings (equivalent to adata.obsm)
# assays(sce)       - named list of matrices (equivalent to adata.layers)
```

### Seurat Object (R)

```r
# Core structure
library(Seurat)
# srat[["RNA"]]@counts   - raw counts
# srat[["RNA"]]@data     - normalized data
# srat@meta.data          - cell metadata
# Embeddings(srat, "pca") - PCA coordinates
# Embeddings(srat, "umap")- UMAP coordinates
```

### Structure Comparison

| Concept | AnnData (Python) | SingleCellExperiment (R) | Seurat (R) |
|---------|-------------------|--------------------------|------------|
| Count matrix | `adata.X` | `counts(sce)` | `srat[["RNA"]]@counts` |
| Cell metadata | `adata.obs` | `colData(sce)` | `srat@meta.data` |
| Gene metadata | `adata.var` | `rowData(sce)` | `srat[["RNA"]]@meta.features` |
| Embeddings | `adata.obsm` | `reducedDims(sce)` | `Embeddings(srat)` |
| Extra matrices | `adata.layers` | `assays(sce)` | `srat[["RNA"]]@layers` |

> [!WARNING]
> AnnData stores cells as rows and genes as columns (cells x genes).
> R objects (SingleCellExperiment, Seurat) store genes as rows and cells as columns
> (genes x cells). This transposition is handled automatically by conversion tools,
> but be aware of it when writing custom interop code.

---

## 5. File Formats

### Primary Formats

| Format | Extension | Ecosystem | Multi-modal | Notes |
|--------|-----------|-----------|-------------|-------|
| H5AD | `.h5ad` | Python/AnnData | No | Standard for scverse; HDF5-backed |
| H5MU | `.h5mu` | Python/MuData | Yes | Multi-modal extension of H5AD |
| Loom | `.loom` | Python/R | No | HDF5-based; used by velocyto, SCENIC |
| RDS | `.rds` | R | Depends | Native R serialization; Seurat/SCE objects |
| 10x H5 | `.h5` | Any | Feature types | CellRanger output; widely supported |

### MuData / Muon for Multi-Modal Data

MuData extends AnnData to multi-modal experiments (e.g., RNA + ATAC, CITE-seq):

```python
import muon as mu

# Read multi-modal data
mdata = mu.read("multiome.h5mu")

# Access individual modalities
rna = mdata.mod['rna']   # AnnData for RNA
atac = mdata.mod['atac']  # AnnData for ATAC

# Joint embedding
mu.pp.intersect_obs(mdata)  # Keep cells present in all modalities
```

> [!TIP]
> H5AD is the recommended format for archiving and sharing single-cell data.
> It preserves all AnnData slots (X, obs, var, obsm, layers, uns) in a single
> portable file. For multi-modal data, use H5MU.

---

## 6. Cross-Ecosystem Interoperability

Sharing data between Python and R ecosystems is a common requirement.
H5AD serves as the lingua franca for cross-ecosystem exchange.

### Python: Write H5AD

```python
# Write H5AD for cross-ecosystem sharing
adata.write_h5ad("processed.h5ad")

# Python-R interop with rpy2
import anndata2ri
anndata2ri.activate()
```

### R: Read H5AD (Bioconductor)

```r
# Read H5AD in R (Bioconductor)
library(zellkonverter)
sce <- readH5AD("processed.h5ad")

# Seurat conversion
library(SeuratDisk)
Convert("processed.h5ad", dest = "h5seurat")
srat <- LoadH5Seurat("processed.h5seurat")
```

### R to Python

```r
# Save from R for Python consumption
library(zellkonverter)
writeH5AD(sce, "from_r.h5ad")
```

```python
# Read in Python
adata = sc.read_h5ad("from_r.h5ad")
```

### Interop Tools Summary

| Tool | Direction | Objects | Notes |
|------|-----------|---------|-------|
| `zellkonverter` | H5AD <-> SCE | AnnData <-> SingleCellExperiment | Recommended for Bioconductor |
| `SeuratDisk` | H5AD <-> Seurat | AnnData <-> Seurat | Via intermediate h5seurat format |
| `anndata2ri` | In-memory | AnnData <-> SCE | Direct conversion via rpy2 (no disk I/O) |
| `rpy2` / `reticulate` | In-memory | General | Python-from-R or R-from-Python bridging |

> [!WARNING]
> Not all AnnData fields survive round-trip conversion. Unstructured metadata
> (`adata.uns`) and complex dtypes in `obs`/`var` may be lost or altered.
> Always verify critical fields after conversion.

> [!TIP]
> For reproducible cross-ecosystem workflows, save an H5AD checkpoint after each
> major analysis step. This allows collaborators in either ecosystem to pick up
> from any point without re-running upstream steps.

---

## Quick Reference: Minimal Scanpy Workflow

```python
import scanpy as sc

# 1. Load data
adata = sc.read_10x_h5("filtered_feature_bc_matrix.h5")

# 2. QC and filtering (see quality_control skill for full workflow)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], inplace=True)
adata = adata[adata.obs['pct_counts_mt'] < 20].copy()
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)

# 3. Normalize and select features
adata.layers['counts'] = adata.X.copy()
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)

# 4. Dimensionality reduction and clustering
sc.pp.pca(adata, n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
sc.tl.umap(adata)
sc.tl.leiden(adata, resolution=0.5)

# 5. Save
adata.write_h5ad("processed.h5ad")
```

---

## Best Practices Summary

1. **Retain raw data**: Always keep `raw_feature_bc_matrix` and store raw counts in `adata.layers['counts']` before normalization.
2. **Use standard formats**: H5AD for single-modality, H5MU for multi-modal. Avoid proprietary or transient formats for archival.
3. **Document processing**: Record tool versions, reference genome, and pipeline parameters in `adata.uns` or companion metadata.
4. **Validate conversions**: After any Python-R conversion, check matrix dimensions, metadata integrity, and embedding presence.
5. **Choose frameworks deliberately**: Use Scanpy/scverse for Python-centric or deep learning workflows; Seurat or Bioconductor for R-centric or statistically focused workflows.

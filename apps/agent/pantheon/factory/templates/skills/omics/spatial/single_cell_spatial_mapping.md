---
id: single_cell_spatial_mapping
name: Single-Cell to Spatial Mapping
description: |
  Map single-cell RNA-seq data to spatial transcriptomics data using
  optimal transport (MOSCOT). Enables gene imputation and cell type transfer.
tags: [spatial, mapping, imputation, moscot]
---

# Single-Cell to Spatial Mapping with MOSCOT

This skill describes how to use [moscot](https://moscot.readthedocs.io/en/latest/)
to map single-cell data to spatial data with Optimal Transport, and how to impute
genes/features that are not observed in the spatial data.

## Prerequisites

```bash
pip install moscot
```

## Workflow

### 1. Load the Data

```python
adata_sc = ...  # single-cell data (AnnData)
adata_sp = ...  # spatial data (AnnData)
```

### 2. Pre-Mapping Checks

Before mapping, verify:

a. **Cell and gene counts**: Check dimensions of both datasets
b. **Normalization**: scRNA-seq should be normalized (normalize_total + log1p).
   Spatial data (e.g. MERFISH) should use raw expression values — do NOT normalize spatial data.

```python
import scanpy as sc

# Normalize scRNA-seq only
sc.pp.normalize_total(adata_sc, target_sum=1e4)
sc.pp.log1p(adata_sc)

# Verify ranges
print(f"SC: {adata_sc.shape}, SP: {adata_sp.shape}")
print(f"SC X range: {adata_sc.X.min():.2f} - {adata_sc.X.max():.2f}")
print(f"SP X range: {adata_sp.X.min():.2f} - {adata_sp.X.max():.2f}")
```

c. **Shared genes**: Subset both datasets to their intersection before mapping.

```python
shared_genes = adata_sc.var_names.intersection(adata_sp.var_names)
print(f"Shared genes: {len(shared_genes)}")

adata_sc_shared = adata_sc[:, shared_genes].copy()
adata_sp_shared = adata_sp[:, shared_genes].copy()
```

### 3. Filter Cell Cycle Genes (Optional)

Cell cycle genes can dominate the mapping. Consider filtering:

```python
s_genes = ["RRM2", "DSCC1", "PRIM1", "GMNN", "CCNE2", "E2F8", "EXO1", 
           "RAD51AP1", "WDR76", "USP1", "NASP", "CASP8AP2", "RAD51", 
           "MSH2", "PCNA", "FEN1", "RRM1", "CDC6", "CLSPN", "POLA1", 
           "TYMS", "SLBP", "CENPU", "MCM5", "TIPIN", "MCM4", "MCM6", 
           "RFC2", "UNG", "CHAF1B", "CDC45", "HELLS", "MRPL36", "POLR1B", 
           "BLM", "CDCA7", "DTL", "UHRF1", "UBR7", "MCM7", "GINS2"]

g2m_genes = ["NEK2", "CDCA8", "SMC4", "LBR", "ANP32E", "HMMR", "AURKB", 
             "CDC20", "KIF11", "RANGAP1", "CDK1", "GTSE1", "TPX2", "NDC80", 
             "CKAP2", "MKI67", "ECT2", "G2E3", "CENPE", "NCAPD2", "PIMREG", 
             "CDC25C", "CENPF", "TUBB4B", "CENPA", "BUB1", "PSRC1", "NUF2", 
             "TOP2A", "GAS2L3", "NUSAP1", "TACC3", "CBX5", "AURKA", "CDCA3", 
             "KIF2C", "BIRC5", "HMGB2", "KIF20B", "TTK", "TMPO", "UBE2C", 
             "CKS2", "DLGAP5", "CKAP2L", "ANLN", "CKAP5", "HJURP", "CCNB2", 
             "CKS1B", "CDCA2", "KIF23", "CTCF"]

cellcycle_genes = {g.upper() for g in s_genes + g2m_genes}

shared_genes = adata_sc.var_names.intersection(adata_sp.var_names)
filter_genes = [g for g in shared_genes if g.upper() not in cellcycle_genes]

adata_sc_filt = adata_sc[:, filter_genes].copy()
adata_sp_filt = adata_sp[:, filter_genes].copy()
```

### 4. Perform Mapping

> [!IMPORTANT]
> Always map at **single-cell resolution**. Do NOT aggregate scRNA-seq into cluster
> centroids before mapping — this loses cell-level heterogeneity and degrades results.

```python
from moscot.problems.space import MappingProblem

mp = MappingProblem(adata_sc_filt, adata_sp_filt)
mp = mp.prepare(sc_attr=None, xy_callback="local-pca")
mp = mp.solve(alpha=0, tau_a=1, tau_b=0.9, device="cpu")
```

**Parameters:**
- `prepare`:
  - `sc_attr=None`: let MOSCOT handle source cost automatically
  - `xy_callback="local-pca"`: use local PCA representation for spatial coordinates
- `solve`:
  - `alpha`: interpolation between linear OT (0) and Gromov-Wasserstein (1).
    Use `alpha=0` (pure expression-based) when scRNA-seq has no spatial coordinates.
  - `tau_a`: source (scRNA-seq) marginal constraint. `1` = fully balanced, all sc cells contribute.
  - `tau_b`: target (spatial) marginal constraint. `0.9` = slightly relaxed, allows spatial cells
    with no good sc match to receive less mass.

> [!TIP]
> For typical scRNA-seq to spatial mapping, start with `alpha=0, tau_a=1, tau_b=0.9`.

### 5. Get and Scale Transport Matrix

MOSCOT returns the transport matrix with shape `(n_sp, n_sc)`.
The raw matrix is a normalized probability distribution — you **must** scale it
before imputation or label transfer.

```python
import numpy as np

sol = list(mp.solutions.values())[0]
# JAX-backed arrays are read-only; force a writable float32 copy
pi = np.array(sol.transport_matrix, dtype=np.float32, copy=True)
pi *= float(pi.shape[0])  # REQUIRED: scale for correct imputation magnitude
```

### 6. Impute Gene Expression

Impute genes from scRNA-seq to spatial coordinates:

```python
from anndata import AnnData

# pi: (n_sp, n_sc), gexp_sc: (n_sc, n_genes) -> imputed: (n_sp, n_genes)
gexp_sc = adata_sc.X
if hasattr(gexp_sc, 'toarray'):
    gexp_sc = gexp_sc.toarray()
imputed_X = pi.dot(gexp_sc)

adata_imputed = AnnData(
    X=imputed_X, 
    obsm=adata_sp.obsm.copy(),
    obs=adata_sp.obs.copy()
)
adata_imputed.obs_names = adata_sp.obs_names
adata_imputed.var_names = adata_sc.var_names
```

### 7. Cross-Modality Imputation

The transport matrix maps **cells**, not genes. Any feature matrix that shares
the same sc cells can be projected to spatial coordinates using the same `pi`.

This is useful when you have paired multiome data (e.g. scRNA + scATAC from
the same cells): build `pi` from RNA (which has more shared genes with spatial),
then reuse it to impute ATAC or other modalities.

```python
# Example: impute ATAC accessibility to spatial coordinates
# adata_atac must have the SAME cells (same obs index) as adata_sc
atac_X = adata_atac.X
if hasattr(atac_X, 'toarray'):
    atac_X = atac_X.toarray()
imputed_atac = pi.dot(atac_X.astype(np.float32))

adata_imputed_atac = AnnData(
    X=imputed_atac,
    obsm=adata_sp.obsm.copy(),
    obs=adata_sp.obs.copy()
)
adata_imputed_atac.var_names = adata_atac.var_names
```

> [!IMPORTANT]
> The sc cells in the other modality must be identical to (or a subset of) the
> cells used to build `pi`. If they come from multiome experiments, match
> barcodes between RNA and ATAC before mapping.

### 8. Transfer Cell Type Labels

Map cell type annotations from single-cell to spatial:

```python
import numpy as np

def map_labels_via_transport(pi, labels):
    """
    Map labels from source (SC) to target (SP) via transport matrix.
    
    Args:
        pi: Transport matrix, shape (n_sp, n_sc) — already scaled
        labels: Array of labels for SC cells, length n_sc
    
    Returns:
        Mapped labels for SP cells, score matrix, unique labels
    """
    unique_labels, inv = np.unique(labels, return_inverse=True)
    
    # One-hot encoding: M[i, k] = 1 iff labels[i] == unique_labels[k]
    M = np.eye(len(unique_labels), dtype=pi.dtype)[inv]  # (n_sc, n_labels)
    
    # Score for each label at each SP location
    # pi @ M: (n_sp, n_sc) @ (n_sc, n_labels) -> (n_sp, n_labels)
    scores = pi.dot(M)
    
    # Assign label with highest score
    best_idx = scores.argmax(axis=1)
    mapped_labels = unique_labels[best_idx]
    
    return mapped_labels, scores, unique_labels

# Apply
mapped_celltypes, scores, unique_labels = map_labels_via_transport(
    pi, 
    adata_sc.obs['celltype'].values
)
adata_sp.obs['mapped_celltype'] = mapped_celltypes
```

## Large Dataset Handling

For large spatial datasets (>20k cells), split the spatial data into batches
and run mapping independently per batch.

```python
def split_into_batches(adata, batch_size=20000, random_state=None):
    n = adata.shape[0]
    indices = np.arange(n)
    if random_state is not None:
        np.random.seed(random_state)
        np.random.shuffle(indices)
    batches = []
    for i in range(0, n, batch_size):
        batch_idx = indices[i : i + batch_size]
        batches.append(adata[batch_idx].copy())
    return batches

# Split spatial data
sp_batches = split_into_batches(adata_sp_filt, batch_size=20000, random_state=42)

# Map each batch independently
for i, sp_batch in enumerate(sp_batches):
    mp = MappingProblem(adata_sc_filt, sp_batch)
    mp = mp.prepare(sc_attr=None, xy_callback="local-pca")
    mp = mp.solve(alpha=0, tau_a=1, tau_b=0.9, device="cpu")
    
    sol = list(mp.solutions.values())[0]
    pi = np.array(sol.transport_matrix, dtype=np.float32, copy=True)
    pi *= float(pi.shape[0])
    
    # Impute and transfer labels per batch...
    imputed_X = pi.dot(gexp_sc)
    mapped_labels, scores, unique_labels = map_labels_via_transport(
        pi, adata_sc.obs['celltype'].values
    )
    
    # Save per-batch result
    adata_pred = AnnData(X=imputed_X, obsm=sp_batch.obsm.copy(), obs=sp_batch.obs.copy())
    adata_pred.obs['mapped_celltype'] = mapped_labels
    adata_pred.write_h5ad(f'mapping_result_batch_{i}.h5ad')
```

## Quality Assessment

After mapping, visualize results:

```python
import scanpy as sc

# Visualize mapped cell types on spatial coordinates
sc.pl.spatial(adata_sp, color='mapped_celltype', spot_size=1)

# Check score distribution for mapping confidence
max_scores = scores.max(axis=1) / scores.sum(axis=1)
adata_sp.obs['mapping_confidence'] = max_scores
sc.pl.violin(adata_sp, 'mapping_confidence')
```

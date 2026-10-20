---
id: sc_bp_surface_protein
name: "SC Best Practices: Surface Protein (CITE-seq)"
description: |
  Analysis of CITE-seq and surface protein data including QC,
  normalization, and multi-modal integration with RNA.
tags: [CITE-seq, protein, ADT, surface, multimodal, sc-best-practices]
---

# SC Best Practices: Surface Protein (CITE-seq)

Analysis of CITE-seq (Cellular Indexing of Transcriptomes and Epitopes
by Sequencing) data, covering antibody-derived tag (ADT) quality control,
normalization strategies, doublet detection, dimensionality reduction,
and joint RNA + protein analysis.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Technology Overview

### CITE-seq Principle

CITE-seq simultaneously measures surface protein expression (via
antibody-derived tags, ADTs) and mRNA from the same single cell:

```
Cell -> Stained with oligo-conjugated antibodies (ADTs)
     -> Encapsulated in droplet (10x Chromium)
     -> cDNA libraries for:
          1. Gene expression (GEX) -- standard scRNA-seq
          2. Antibody-derived tags (ADT) -- surface protein counts
     -> Sequenced and demultiplexed
```

### Key Features

| Aspect | RNA (GEX) | Protein (ADT) |
|--------|-----------|---------------|
| Features measured | ~20,000 genes | 100-300 proteins |
| Sparsity | High (dropout) | Low (high capture rate) |
| Dynamic range | Wide | Narrower, bimodal |
| Signal distribution | Unimodal (approximately) | Bimodal (negative + positive populations) |
| Background noise | Ambient RNA | Non-specific antibody binding |

### Related Technologies

| Technology | Protein Readout | RNA Readout | Notes |
|------------|----------------|-------------|-------|
| CITE-seq | ADT (oligo-conjugated Ab) | 3'/5' scRNA-seq | Most widely used |
| REAP-seq | DNA-barcoded Ab | 3' scRNA-seq | Similar to CITE-seq |
| ECCITE-seq | ADT | 5' scRNA-seq + TCR/BCR | Multi-modal immune profiling |
| TEA-seq | ADT | scRNA-seq + scATAC | Triple-modal |

---

## 2. Data Loading and Structure

### Loading CITE-seq Data

```python
import scanpy as sc
import muon as mu

# 10x CellRanger output: filtered_feature_bc_matrix.h5
# Contains both GEX and ADT features
adata = sc.read_10x_h5("filtered_feature_bc_matrix.h5", gex_only=False)

# Separate RNA and ADT modalities
adata_rna = adata[:, adata.var['feature_types'] == 'Gene Expression'].copy()
adata_adt = adata[:, adata.var['feature_types'] == 'Antibody Capture'].copy()

# Store as MuData
import mudata as md
mdata = md.MuData({"rna": adata_rna, "adt": adata_adt})

# Verify
print(f"RNA: {adata_rna.shape}")    # e.g., (8000, 20000)
print(f"ADT: {adata_adt.shape}")    # e.g., (8000, 200)
```

### Understanding ADT Feature Names

ADT feature names typically follow the pattern `protein_name` or
`anti-human_protein_name`. Some datasets use prefixes:

```python
# Check ADT feature names
print(adata_adt.var_names[:10])
# Example: ['CD3', 'CD4', 'CD8a', 'CD19', 'CD56', 'CD14', ...]

# Identify isotype controls (negative controls for background estimation)
isotype_controls = [v for v in adata_adt.var_names
                    if 'isotype' in v.lower() or 'IgG' in v]
print(f"Isotype controls: {isotype_controls}")
```

---

## 3. Quality Control

### ADT-Specific QC Metrics

```python
# Calculate ADT QC metrics
sc.pp.calculate_qc_metrics(adata_adt, inplace=True)

# ADT-specific metrics
print(adata_adt.obs[['total_counts', 'n_genes_by_counts']].describe())

# Plot ADT library size distribution
sc.pl.violin(adata_adt, keys=['total_counts', 'n_genes_by_counts'])
```

### Isotype Control Assessment

Isotype controls are non-targeting antibodies included to estimate
background binding. Their counts should be uniformly low:

```python
import matplotlib.pyplot as plt
import numpy as np

# Visualize isotype control distribution
if isotype_controls:
    fig, axes = plt.subplots(1, len(isotype_controls),
                              figsize=(4 * len(isotype_controls), 4))
    if len(isotype_controls) == 1:
        axes = [axes]

    for ax, iso in zip(axes, isotype_controls):
        counts = adata_adt[:, iso].X.toarray().flatten() \
                 if hasattr(adata_adt[:, iso].X, 'toarray') \
                 else adata_adt[:, iso].X.flatten()
        ax.hist(counts, bins=50, color='steelblue', edgecolor='black')
        ax.set_title(iso)
        ax.set_xlabel("Counts")
        ax.set_ylabel("Cells")

    plt.tight_layout()
    plt.show()
```

> [!WARNING]
> High isotype control counts in a cell suggest non-specific antibody binding,
> which affects all ADT measurements for that cell. Consider removing cells
> with isotype control counts above the 95th-99th percentile.

### Joint RNA + ADT QC

```python
# Cells should pass QC in BOTH modalities
# RNA QC
adata_rna.var['mt'] = adata_rna.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata_rna, qc_vars=['mt'], inplace=True)

# Combined filtering
keep_cells = (
    (adata_rna.obs['pct_counts_mt'] < 20) &
    (adata_rna.obs['n_genes_by_counts'] > 200) &
    (adata_rna.obs['n_genes_by_counts'] < 6000) &
    (adata_adt.obs['total_counts'] > 100)  # Minimum ADT library size
)

adata_rna = adata_rna[keep_cells].copy()
adata_adt = adata_adt[keep_cells].copy()
```

---

## 4. Normalization

### Centered Log-Ratio (CLR) Normalization

CLR is the recommended normalization for ADT data. It accounts for
the compositional nature of the data (ADT counts share a total
capture budget per cell):

```python
import numpy as np
from scipy.sparse import issparse

def clr_normalize(adata_adt, axis=0):
    """
    Centered log-ratio normalization for ADT data.

    Parameters
    ----------
    adata_adt : AnnData
        ADT count matrix
    axis : int
        0 = normalize per cell (across proteins), recommended
        1 = normalize per protein (across cells)

    Returns
    -------
    AnnData with CLR-normalized values in .X
    """
    X = adata_adt.X.toarray() if issparse(adata_adt.X) else adata_adt.X.copy()

    # Add pseudocount to avoid log(0)
    X = X + 1

    if axis == 0:  # Per cell (across proteins)
        geometric_mean = np.exp(np.mean(np.log(X), axis=1, keepdims=True))
        X_clr = np.log(X / geometric_mean)
    else:  # Per protein (across cells)
        geometric_mean = np.exp(np.mean(np.log(X), axis=0, keepdims=True))
        X_clr = np.log(X / geometric_mean)

    adata_adt.layers['counts'] = adata_adt.X.copy()
    adata_adt.X = X_clr

    return adata_adt

# Apply CLR normalization
adata_adt = clr_normalize(adata_adt, axis=0)
```

### Muon CLR Normalization

```python
import muon as mu

# CLR normalization via muon (preferred, handles edge cases)
mu.prot.pp.clr(adata_adt)
```

### DSB (Denoised and Scaled by Background) Normalization

DSB uses empty droplets to estimate and remove technical background noise:

```python
# DSB requires empty droplet data
# Load raw (unfiltered) data to get empty droplets
adata_raw = sc.read_10x_h5("raw_feature_bc_matrix.h5", gex_only=False)
adata_raw_adt = adata_raw[:, adata_raw.var['feature_types'] == 'Antibody Capture'].copy()

# Identify empty droplets (low RNA counts)
empty_mask = adata_raw.obs['total_counts'] < 100  # or use CellRanger's cell calling
adata_empty_adt = adata_raw_adt[empty_mask].copy()

# DSB is R-based; use via rpy2
import rpy2.robjects as ro
from rpy2.robjects.packages import importr

dsb = importr('dsb')
# ro.r('''
# normalized <- DSBNormalizeProtein(
#     cell_protein_matrix = cell_adt_matrix,
#     empty_drop_matrix = empty_adt_matrix,
#     denoise.counts = TRUE,
#     use.isotype.control = TRUE,
#     isotype.control.name.vec = c("IgG1", "IgG2a", "IgG2b")
# )
# ''')
```

### Normalization Method Comparison

| Method | Requires Empty Droplets | Handles Background | Recommended For |
|--------|------------------------|-------------------|-----------------|
| **CLR** | No | Partial | Default; always applicable |
| **DSB** | Yes | Yes (explicit denoising) | When raw matrix is available |
| Log-normalization | No | No | Not recommended for ADT |
| Scaling | No | No | Not recommended for ADT |

> [!TIP]
> **CLR** is the recommended default normalization for ADT data. Use **DSB**
> when you have access to empty droplet data, as it provides explicit background
> denoising. Never use standard log-normalization (designed for RNA) on ADT data.

---

## 5. Doublet Detection

ADT data can improve doublet detection because doublets will show
co-expression of mutually exclusive surface markers:

```python
# ADT-informed doublet detection
# Example: CD3+ (T cells) and CD19+ (B cells) should be mutually exclusive
import numpy as np

cd3_positive = adata_adt[:, 'CD3'].X.toarray().flatten() > 1.5  # CLR threshold
cd19_positive = adata_adt[:, 'CD19'].X.toarray().flatten() > 1.5

# Cells positive for both are likely doublets
potential_doublets = cd3_positive & cd19_positive
print(f"Potential T/B doublets: {potential_doublets.sum()} "
      f"({potential_doublets.mean()*100:.1f}%)")

# Combine with RNA-based doublet detection (Scrublet)
import scrublet as scr
scrub = scr.Scrublet(adata_rna.X)
doublet_scores, predicted_doublets = scrub.scrub_doublets()

# Joint doublet call
adata_rna.obs['doublet_adt'] = potential_doublets
adata_rna.obs['doublet_rna'] = predicted_doublets
adata_rna.obs['doublet_either'] = potential_doublets | predicted_doublets
```

---

## 6. Dimensionality Reduction and Integration

### Weighted Nearest Neighbors (WNN)

WNN (from Seurat v4) computes a joint cell-cell similarity graph by
weighting RNA and ADT modalities per cell based on their informativeness:

```python
import muon as mu

# Process RNA modality
sc.pp.normalize_total(adata_rna, target_sum=1e4)
sc.pp.log1p(adata_rna)
sc.pp.highly_variable_genes(adata_rna, n_top_genes=3000)
sc.pp.pca(adata_rna, n_comps=30)

# Process ADT modality (already CLR-normalized)
sc.pp.pca(adata_adt, n_comps=min(18, adata_adt.n_vars - 1))

# Build MuData
mdata = md.MuData({"rna": adata_rna, "adt": adata_adt})

# Compute WNN
mu.pp.neighbors(mdata, key_added='wnn')

# Joint UMAP
mu.tl.umap(mdata, neighbors_key='wnn')

# Clustering on joint graph
mu.tl.leiden(mdata, neighbors_key='wnn', resolution=0.5, key_added='wnn_leiden')
```

### RNA-Only vs. ADT-Only vs. WNN Comparison

```python
# Compare embeddings
fig, axes = plt.subplots(1, 3, figsize=(18, 5))

# RNA UMAP
sc.pp.neighbors(adata_rna, n_neighbors=15, n_pcs=30)
sc.tl.umap(adata_rna)
sc.pl.umap(adata_rna, color='cell_type', ax=axes[0], title='RNA only', show=False)

# ADT UMAP
sc.pp.neighbors(adata_adt, n_neighbors=15, n_pcs=15)
sc.tl.umap(adata_adt)
sc.pl.umap(adata_adt, color='cell_type', ax=axes[1], title='ADT only', show=False)

# WNN UMAP
sc.pl.embedding(mdata, basis='X_umap', color='cell_type', ax=axes[2],
                title='WNN (RNA + ADT)', show=False)

plt.tight_layout()
plt.show()
```

---

## 7. totalVI: Joint Probabilistic Modeling

totalVI (from scvi-tools) jointly models RNA and protein data with a
variational autoencoder, handling both modalities in a unified framework:

```python
import scvi

# Prepare AnnData with both modalities
adata_combined = adata_rna.copy()
adata_combined.obsm['protein_expression'] = adata_adt.X.toarray() \
    if issparse(adata_adt.X) else adata_adt.X

# Store protein names
protein_names = adata_adt.var_names.tolist()

# Setup totalVI
scvi.model.TOTALVI.setup_anndata(
    adata_combined,
    protein_expression_obsm_key="protein_expression",
    layer="counts",          # Raw counts layer for RNA
    batch_key="batch",       # Optional batch key
)

# Train model
model = scvi.model.TOTALVI(
    adata_combined,
    n_latent=20,
    latent_distribution="normal",
)
model.train(max_epochs=400, early_stopping=True)

# Get joint latent representation
adata_combined.obsm['X_totalVI'] = model.get_latent_representation()

# Get denoised protein expression
denoised_protein = model.get_normalized_expression(
    n_samples=25,
    return_mean=True,
    transform_batch=None,
)

# Cluster on totalVI embedding
sc.pp.neighbors(adata_combined, use_rep='X_totalVI')
sc.tl.umap(adata_combined)
sc.tl.leiden(adata_combined, resolution=0.5)
```

### totalVI Advantages

- Jointly models RNA and protein in a single generative model
- Handles missing proteins (some cells may lack ADT measurements)
- Provides denoised protein expression estimates
- Integrates batch correction naturally
- Enables differential expression testing for both modalities

---

## 8. Cell Annotation Using Both Modalities

Surface proteins are excellent markers for cell-type annotation,
especially for immune cells:

```python
# Common CITE-seq markers for immune cell annotation
immune_markers = {
    'T cells': {'rna': ['CD3D', 'CD3E'], 'adt': ['CD3']},
    'CD4 T': {'rna': ['CD4', 'IL7R'], 'adt': ['CD4']},
    'CD8 T': {'rna': ['CD8A', 'CD8B'], 'adt': ['CD8a']},
    'B cells': {'rna': ['MS4A1', 'CD79A'], 'adt': ['CD19', 'CD20']},
    'NK cells': {'rna': ['NKG7', 'GNLY'], 'adt': ['CD56', 'CD16']},
    'Monocytes': {'rna': ['CD14', 'LYZ'], 'adt': ['CD14']},
    'DC': {'rna': ['FCER1A', 'CST3'], 'adt': ['CD11c', 'HLA-DR']},
}

# Visualize protein markers on UMAP
adt_markers = ['CD3', 'CD4', 'CD8a', 'CD19', 'CD56', 'CD14']
sc.pl.umap(adata_adt, color=adt_markers, ncols=3, vmax='p99')
```

> [!TIP]
> Protein expression often provides cleaner separation of cell types than RNA
> alone, especially for surface markers. When RNA and protein markers disagree,
> investigate potential technical issues (antibody quality, normalization
> artifacts) before biological interpretation.

---

## 9. Batch Correction for CITE-seq

```python
# Harmony on joint WNN embedding
import harmonypy as hp

# Run Harmony on totalVI latent space
ho = hp.run_harmony(
    adata_combined.obsm['X_totalVI'],
    adata_combined.obs,
    'batch',
)
adata_combined.obsm['X_totalVI_harmony'] = ho.Z_corr.T

# Alternatively: use totalVI's built-in batch correction
# (already handled if batch_key is set during setup)
```

---

## Best Practices Summary

1. **Use CLR normalization for ADT**: Never apply standard RNA normalization (log-normalization) to protein data. CLR or DSB are appropriate.
2. **Leverage isotype controls**: Use isotype control antibodies to assess and correct non-specific binding background.
3. **Filter on both modalities**: Cells must pass QC in both RNA and ADT to be retained.
4. **Use WNN or totalVI for joint analysis**: These methods weight each modality per cell, avoiding dominance by one modality.
5. **Exploit bimodal ADT distributions**: ADT data often shows clear positive/negative populations, enabling confident gating-style annotation.
6. **Enhance doublet detection with ADT**: Co-expression of mutually exclusive surface markers (e.g., CD3 + CD19) is a strong doublet signal.
7. **Compare uni-modal vs. multi-modal**: Always check whether joint analysis improves over RNA-only or ADT-only clustering. If not, investigate data quality issues.
8. **Store raw counts**: Keep raw ADT counts in `adata_adt.layers['counts']` before normalization.

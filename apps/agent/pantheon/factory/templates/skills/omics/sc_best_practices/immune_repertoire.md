---
id: sc_bp_immune_repertoire
name: "SC Best Practices: Immune Receptor Repertoire"
description: |
  Analysis of adaptive immune receptor repertoire (TCR/BCR) data
  including clonotype analysis, diversity, and integration with transcriptomics.
tags: [TCR, BCR, immune, repertoire, clonotype, scirpy, sc-best-practices]
---

# SC Best Practices: Immune Receptor Repertoire (TCR/BCR)

Analysis of single-cell adaptive immune receptor repertoire data,
covering data loading, chain quality control, clonotype definition,
clonal expansion, diversity metrics, V(D)J gene usage, and
integration with transcriptomic data.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Background

### Adaptive Immune Receptors

Adaptive immune cells express unique antigen receptors generated through
V(D)J recombination:

| Receptor | Cell Type | Chains | Diversity Mechanism |
|----------|-----------|--------|---------------------|
| **TCR** (T cell receptor) | T cells | alpha + beta (or gamma + delta) | V(D)J recombination + junctional diversity |
| **BCR** (B cell receptor) | B cells | Heavy (IgH) + Light (IgK or IgL) | V(D)J recombination + somatic hypermutation |

### Key Terminology

- **Clonotype**: A group of cells sharing the same receptor sequence (defined by CDR3 region and/or V/J gene usage)
- **CDR3**: Complementarity-determining region 3 -- the most variable part of the receptor; primary determinant of antigen specificity
- **V(D)J genes**: Variable, Diversity, and Joining gene segments recombined to form the receptor
- **Clonal expansion**: Proliferation of cells with the same clonotype (indicates antigen-driven response)

---

## 2. Data Loading and Merging

### scirpy for Immune Receptor Analysis

scirpy is the primary Python tool for immune receptor repertoire analysis,
integrated with the scverse ecosystem:

```python
import scirpy as ir
import scanpy as sc

# Load VDJ data from 10x CellRanger
adata_airr = ir.io.read_10x_vdj("filtered_contig_annotations.csv")

# Inspect VDJ data structure
print(adata_airr.obs.columns.tolist())
# Contains: IR_VJ_1_*, IR_VDJ_1_*, IR_VJ_2_*, IR_VDJ_2_* columns
# VJ = light chain (BCR) or alpha chain (TCR)
# VDJ = heavy chain (BCR) or beta chain (TCR)
```

### Merging with Gene Expression Data

```python
# Load gene expression data
adata_gex = sc.read_10x_h5("filtered_feature_bc_matrix.h5")

# Standard GEX preprocessing
sc.pp.filter_cells(adata_gex, min_genes=200)
sc.pp.filter_genes(adata_gex, min_cells=3)
adata_gex.layers['counts'] = adata_gex.X.copy()
sc.pp.normalize_total(adata_gex, target_sum=1e4)
sc.pp.log1p(adata_gex)
sc.pp.highly_variable_genes(adata_gex, n_top_genes=3000)
sc.pp.pca(adata_gex, n_comps=50)
sc.pp.neighbors(adata_gex, n_neighbors=15, n_pcs=40)
sc.tl.umap(adata_gex)

# Merge VDJ with GEX (left join: keep all GEX cells, add VDJ where available)
adata = ir.pp.merge_airr(adata_gex, adata_airr)

# Check how many cells have receptor information
has_ir = adata.obs['IR_VDJ_1_junction_aa'].notna()
print(f"Cells with IR: {has_ir.sum()} / {len(adata)} ({has_ir.mean()*100:.1f}%)")
```

> [!TIP]
> The merge is a **left join** by default -- all cells from GEX are kept,
> and VDJ information is added where barcodes match. Cells without detected
> receptors will have NaN in VDJ columns. This is normal; not all T/B cells
> have successfully sequenced receptors.

---

## 3. Chain Quality Control

### Chain Pairing and Filtering

```python
# Inspect chain quality
ir.tl.chain_qc(adata)

# Chain pairing categories
print(adata.obs['chain_pairing'].value_counts())
# Expected categories:
# - single pair: One VJ + one VDJ chain (standard)
# - extra VJ/VDJ: Additional chains detected (possible doublet or dual-receptor cell)
# - orphan VJ/VDJ: Only one chain detected
# - no IR: No receptor detected
# - multichain: Multiple chains (likely doublet)

# Visualize chain pairing
ir.pl.group_abundance(adata, groupby='chain_pairing', target_col='cell_type')
```

### Filtering Recommendations

```python
# Remove cells with ambiguous chain pairing (likely doublets)
# Keep: single pair, extra VJ, extra VDJ (some dual-receptor cells are real)
# Remove: multichain (likely doublets)
adata = adata[
    adata.obs['chain_pairing'].isin(['single pair', 'extra VJ', 'extra VDJ',
                                      'orphan VJ', 'orphan VDJ', 'no IR'])
].copy()

# For stringent analysis, keep only single-pair cells
adata_strict = adata[adata.obs['chain_pairing'] == 'single pair'].copy()
```

---

## 4. Clonotype Definition

Clonotypes can be defined at different levels of stringency:

### Sequence Identity-Based

```python
# Define clonotypes based on CDR3 amino acid sequence identity
ir.pp.ir_dist(adata, metric="identity", sequence="aa")

# Define clonotype clusters
ir.tl.define_clonotype_clusters(
    adata,
    receptor_arms="all",        # Require both chains to match
    dual_ir="primary_only",     # Use only primary chain for dual-receptor cells
    sequence="aa",              # Amino acid sequence
    metric="identity",          # Exact match
)

# Alternatively: define clonotypes using CDR3 nucleotide sequence
ir.pp.ir_dist(adata, metric="identity", sequence="nt")
ir.tl.define_clonotype_clusters(
    adata,
    receptor_arms="all",
    dual_ir="primary_only",
    sequence="nt",
    metric="identity",
)
```

### Sequence Similarity-Based

For grouping clonotypes with similar but not identical CDR3 sequences
(e.g., convergent selection):

```python
# Levenshtein distance-based clustering
ir.pp.ir_dist(
    adata,
    metric="alignment",     # Smith-Waterman alignment
    sequence="aa",
    cutoff=15,              # Maximum distance to connect
)

ir.tl.define_clonotype_clusters(
    adata,
    receptor_arms="all",
    dual_ir="primary_only",
    sequence="aa",
    metric="alignment",
)
```

### Clonotype Definition Parameters

| Parameter | Options | Effect |
|-----------|---------|--------|
| `receptor_arms` | `"all"`, `"any"`, `"VJ"`, `"VDJ"` | Which chains must match |
| `dual_ir` | `"primary_only"`, `"any"`, `"all"` | How to handle dual-receptor cells |
| `sequence` | `"aa"`, `"nt"` | Amino acid or nucleotide matching |
| `metric` | `"identity"`, `"alignment"`, `"hamming"` | Matching stringency |

> [!TIP]
> For standard clonotype analysis, use `receptor_arms="all"` with
> `sequence="aa"` and `metric="identity"`. This requires both chains to
> have identical CDR3 amino acid sequences, which is the most common
> definition in the literature.

---

## 5. Clonal Expansion Analysis

### Quantifying Expansion

```python
# Compute clonotype sizes
ir.tl.clonal_expansion(adata, groupby='cell_type')

# Visualize clonal expansion per cell type
ir.pl.clonal_expansion(
    adata,
    groupby='cell_type',
    clip_at=4,  # Group all clonotypes with >= 4 cells
    normalize=True,
)
```

### Clonal Expansion Categories

```python
# Categorize expansion levels
def categorize_expansion(adata, clonotype_key='clone_id'):
    clone_sizes = adata.obs[clonotype_key].value_counts()
    size_map = clone_sizes.to_dict()
    adata.obs['clone_size'] = adata.obs[clonotype_key].map(size_map)

    adata.obs['expansion_category'] = pd.cut(
        adata.obs['clone_size'],
        bins=[0, 1, 2, 5, 20, float('inf')],
        labels=['Singleton', 'Doublet', 'Small (3-5)',
                'Medium (6-20)', 'Large (>20)']
    )
    return adata

adata = categorize_expansion(adata, clonotype_key='clone_id')
sc.pl.umap(adata, color='expansion_category')
```

> [!CAUTION]
> **Clonal expansion bias**: When comparing clonal expansion across conditions
> or cell types, filter out duplicate TCR/BCR sequences first to prevent
> inflated expansion counts caused by technical duplicates or ambient receptor
> contamination. One approach is to randomly subsample to one cell per clonotype
> per sample for certain downstream analyses.

---

## 6. Repertoire Diversity

### Diversity Metrics

```python
# Compute diversity metrics per group
ir.tl.alpha_diversity(adata, groupby='sample', metric='shannon')
ir.tl.alpha_diversity(adata, groupby='sample', metric='D50')

# Compare diversity across conditions
ir.pl.alpha_diversity(adata, groupby='sample', metric='shannon')
```

### Common Diversity Indices

| Metric | Description | Interpretation |
|--------|-------------|----------------|
| Shannon entropy | Information-theoretic diversity | Higher = more diverse repertoire |
| Simpson index | Probability two random cells share a clonotype | Higher = less diverse (dominance) |
| D50 | Fraction of clonotypes making up 50% of cells | Lower = more dominated by expanded clones |
| Chao1 | Estimated total clonotype richness | Accounts for unobserved clonotypes |

---

## 7. V(D)J Gene Usage

### Gene Usage Analysis

```python
# V gene usage per cell type
ir.pl.vdj_usage(
    adata,
    full_identifier=False,     # Show gene name only (not allele)
    max_ribbons=30,
    fig_kws={'figsize': (12, 6)},
)

# Gene usage by condition
ir.tl.repertoire_overlap(adata, groupby='condition', metric='jaccard')
ir.pl.repertoire_overlap(adata, groupby='condition', metric='jaccard')
```

### Spectratype Analysis (CDR3 Length Distribution)

CDR3 length distribution provides a global view of repertoire diversity.
Healthy repertoires show a Gaussian-like distribution; skewed distributions
suggest clonal expansion or restricted diversity:

```python
import matplotlib.pyplot as plt
import numpy as np

# CDR3 length distribution
cdr3_lengths = adata.obs['IR_VDJ_1_junction_aa'].dropna().str.len()

fig, ax = plt.subplots(figsize=(8, 4))
ax.hist(cdr3_lengths, bins=range(5, 30), edgecolor='black', alpha=0.7)
ax.set_xlabel("CDR3 Length (amino acids)")
ax.set_ylabel("Number of Cells")
ax.set_title("CDR3 Length Distribution (Spectratype)")
plt.tight_layout()
plt.show()

# Per-condition spectratype
for condition in adata.obs['condition'].unique():
    mask = adata.obs['condition'] == condition
    cdr3 = adata.obs.loc[mask, 'IR_VDJ_1_junction_aa'].dropna().str.len()
    plt.hist(cdr3, bins=range(5, 30), alpha=0.5, label=condition, density=True)
plt.legend()
plt.xlabel("CDR3 Length")
plt.ylabel("Density")
plt.show()
```

---

## 8. Specificity Analysis

### Approaches to Antigen Specificity

| Approach | Description | Data Required |
|----------|-------------|---------------|
| Database matching | Match CDR3 to known antigen-specific sequences | VDJdb, McPAS-TCR, IEDB |
| Tetramer/dextramer | Experimental antigen binding + sequencing | Specialized experiment |
| Clustering by CDR3 similarity | Group sequences with similar binding properties | Sequence data only |
| TCR-pMHC modeling | Computational prediction of binding | Structural models |

```python
# Match against VDJdb (database of known antigen-specific TCRs)
# Download VDJdb: https://vdjdb.cdr3.net/
import pandas as pd

vdjdb = pd.read_csv("vdjdb_full.txt", sep='\t')
known_cdr3 = set(vdjdb['CDR3'].unique())

# Check for known specificities
adata.obs['known_specificity'] = adata.obs['IR_VDJ_1_junction_aa'].isin(known_cdr3)
print(f"Cells with known specificity: {adata.obs['known_specificity'].sum()}")
```

---

## 9. Integration with Gene Expression

### Integrated Analysis Methods

| Method | Modalities | Approach | Key Feature |
|--------|------------|----------|-------------|
| **CoNGA** | TCR/BCR + GEX | Graph overlap testing | Identifies TCR-GEX correlations |
| **TESSA** | TCR + GEX | Bayesian model | Imputes TCR functional states |
| **mvTCR** | TCR + GEX | Multi-view VAE | Joint TCR-GEX embedding |
| **Benisse** | BCR + GEX | Network-based | BCR evolution with expression |

### CoNGA: Clonotype-Neighborhood Graph Analysis

```python
# CoNGA identifies cells where TCR similarity and GEX similarity co-occur
# This suggests antigen-driven transcriptomic programs

# Install: pip install conga
# CoNGA requires specific input format; see documentation
# https://github.com/phbradley/conga
```

### Joint Embedding of TCR + GEX

```python
# mvTCR: Multi-view TCR analysis
# Creates a joint latent space from TCR sequences and gene expression
# Enables clustering that reflects both clonotype structure and cell state

# Basic approach: concatenate TCR features with GEX embedding
# Step 1: Encode TCR as numerical features
from sklearn.preprocessing import LabelEncoder

# One-hot encode V/J genes
v_gene_encoder = LabelEncoder()
adata.obs['v_gene_encoded'] = v_gene_encoder.fit_transform(
    adata.obs['IR_VDJ_1_v_call'].fillna('unknown')
)

# Step 2: Use CDR3 sequence embeddings (e.g., from ESM or custom model)
# Step 3: Concatenate with GEX PCA embedding for joint analysis
```

---

## 10. Multi-Sample Comparisons

### Comparing Repertoires Across Conditions

```python
# Repertoire overlap between samples/conditions
ir.tl.repertoire_overlap(
    adata,
    groupby='condition',
    target_col='clone_id',
    metric='jaccard',    # Options: 'jaccard', 'overlap', 'morisita_horn'
)
ir.pl.repertoire_overlap(adata, groupby='condition')

# Shared clonotypes across samples
ir.pl.venn(adata, groupby='sample', target_col='clone_id')
```

---

## Best Practices Summary

1. **Merge VDJ and GEX early**: Use `ir.pp.merge_airr()` as a left join to preserve all GEX cells, adding VDJ information where available.
2. **Check chain quality**: Inspect chain pairing categories. Remove multichain cells (likely doublets) while retaining orphan chains (incomplete sequencing is common).
3. **Define clonotypes consistently**: Document your clonotype definition parameters (receptor_arms, sequence, metric) as they significantly affect all downstream analyses.
4. **Filter duplicate TCRs to prevent clonal expansion bias**: When performing differential expression or trajectory analysis, subsample to one cell per clonotype to avoid bias from expanded clones dominating the signal.
5. **Use appropriate diversity metrics**: Shannon entropy for general diversity, D50 for dominance assessment, Chao1 for richness estimation.
6. **Validate expanded clonotypes**: Large clonal expansions should be validated against known antigen databases and correlated with clinical/experimental context.
7. **Normalize for sampling depth**: When comparing diversity across samples, account for differences in cell numbers (rarefaction or downsampling).
8. **Consider both chains**: Clonotype definitions using both chains (`receptor_arms="all"`) are more specific but may miss shared single-chain specificities.

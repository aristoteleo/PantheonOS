---
id: sc_bp_bulk_deconvolution
name: "SC Best Practices: Bulk Deconvolution"
description: |
  Reference-based deconvolution of bulk RNA-seq using single-cell references.
  Methods, workflows, and validation approaches.
tags: [deconvolution, bulk, reference, sc-best-practices]
---

# SC Best Practices: Bulk Deconvolution

Estimating cell-type proportions from bulk RNA-seq using single-cell
reference atlases. Covers mathematical foundations, method selection,
preprocessing requirements, and validation strategies.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Mathematical Foundation

Bulk deconvolution rests on a linear mixture model:

```
y = X * b
```

Where:
- **y** = bulk expression profile (genes x 1)
- **X** = cell-type signature matrix (genes x cell types)
- **b** = cell-type proportions vector (cell types x 1)

The goal is to estimate **b** given **y** and **X** (derived from single-cell data).

> [!TIP]
> This linear model assumes that bulk expression is a weighted sum of cell-type
> expression profiles. While this is a simplification (it ignores cell-cell
> interactions and non-linear effects), it works well in practice for most tissues.

---

## 2. Method Categories

### Linear Regression-Based Methods

| Method | Approach | Key Feature |
|--------|----------|-------------|
| CIBERSORTx | Nu-support vector regression | Batch correction for cross-platform use |
| MuSiC | Weighted non-negative least squares | Multi-subject, gene weighting by variance |
| DWLS | Dampened weighted least squares | Iterative re-weighting to reduce bias |
| SCDC | Weighted non-negative least squares | Ensemble across multiple references |
| BisqueRNA | Weighted regression | Bulk reference-assisted decomposition |

### Enrichment-Based Methods

| Method | Approach | Key Feature |
|--------|----------|-------------|
| xCell | Gene set enrichment | Ranks-based, no reference count matrix needed |

### Deep Learning Methods

| Method | Approach | Key Feature |
|--------|----------|-------------|
| Scaden | Deep neural network | Trains on simulated pseudobulk samples |

> [!TIP]
> For most applications, **MuSiC** and **CIBERSORTx** are well-validated defaults.
> Use **Scaden** when you have a large, high-quality single-cell reference and
> want to avoid explicit signature matrix construction.

---

## 3. Input Data Requirements

### Normalization Rules

Deconvolution methods expect data in **linear scale** (not log-transformed):

| Requirement | Do | Do NOT |
|-------------|-----|---------|
| Scale | Raw counts or library-size normalized (e.g., CPM, TPM) | Log-transformed values |
| Normalization | Library-size normalization (total count) | Row scaling, z-score, quantile normalization |
| Genes | Use original gene identifiers matching reference | Aggregate or collapse gene sets |

```python
import scanpy as sc

# CORRECT: Use raw counts or simple library-size normalization
adata_ref = sc.read_h5ad("reference_atlas.h5ad")

# Ensure raw counts are available
if 'counts' in adata_ref.layers:
    adata_ref.X = adata_ref.layers['counts'].copy()

# Simple CPM normalization (linear scale) -- acceptable for deconvolution
sc.pp.normalize_total(adata_ref, target_sum=1e6)
# Do NOT log-transform for deconvolution input
```

> [!WARNING]
> Applying row scaling, z-score normalization, or quantile normalization to
> either the bulk or reference data will break the linear mixture assumption
> and produce unreliable proportion estimates.

---

## 4. Feature Selection

Selecting informative marker genes is critical for deconvolution accuracy.

### Stringent Marker Selection

```python
import scanpy as sc

# Identify marker genes per cell type
sc.tl.rank_genes_groups(adata_ref, groupby='cell_type', method='wilcoxon')

# Extract top markers with stringent thresholds
markers = sc.get.rank_genes_groups_df(adata_ref, group=None)
markers = markers[
    (markers['logfoldchanges'] > 1.5) &
    (markers['pvals_adj'] < 0.01)
]
marker_genes = markers['names'].unique().tolist()
```

### AutoGeneS: Multi-Objective Marker Selection

AutoGeneS uses a Pareto optimization approach to balance correlation minimization
between cell types and variance maximization within cell types:

```python
import autogenes as ag

# Build signature matrix from reference
ag.init(adata_ref)

# Optimize marker selection (multi-objective)
ag.optimize(ngen=200, seed=42, mode='fixed', nfeatures=400)

# Retrieve optimized gene set
pareto_genes = ag.select(index=0)  # Select from Pareto front
```

> [!TIP]
> AutoGeneS is particularly useful when cell types are closely related (e.g.,
> CD4 naive vs. CD4 memory T cells) and standard DE-based marker selection
> yields correlated signatures.

---

## 5. MuSiC Workflow (via rpy2)

MuSiC is an R package; use it from Python via rpy2:

```python
import anndata2ri
import rpy2.robjects as ro
from rpy2.robjects.packages import importr

# Activate anndata-to-R conversion
anndata2ri.activate()

# Import R packages
music = importr('MuSiC')
biobase = importr('Biobase')
base = importr('base')

# Prepare single-cell reference (ExpressionSet format)
ro.globalenv['sc_eset'] = adata_ref  # auto-converted via anndata2ri

# Prepare bulk data
ro.globalenv['bulk_eset'] = adata_bulk

# Run MuSiC deconvolution
ro.r('''
library(MuSiC)
results <- music_prop(
    bulk.mtx = exprs(bulk_eset),
    sc.sce = sc_eset,
    clusters = "cell_type",   # Column in colData with cell-type labels
    samples = "sample_id",    # Column in colData with subject/sample IDs
    verbose = TRUE
)

# Extract estimated proportions
proportions <- results$Est.prop.weighted
''')

# Retrieve results in Python
import rpy2.robjects as ro
import pandas as pd
proportions = ro.r('as.data.frame(proportions)')
proportions_df = pd.DataFrame(proportions)
```

> [!WARNING]
> MuSiC requires multiple subjects/samples in the single-cell reference to
> estimate cross-subject variability. A single-sample reference will produce
> unreliable variance estimates.

---

## 6. Creating Pseudobulk for Validation

Generate pseudobulk samples from single-cell data with known proportions
to benchmark deconvolution accuracy:

```python
import numpy as np
import pandas as pd
from scipy.sparse import issparse

def create_pseudobulk(adata, cell_type_col='cell_type', n_samples=100,
                       n_cells_per_sample=500, seed=42):
    """
    Create pseudobulk samples with known cell-type proportions.

    Parameters
    ----------
    adata : AnnData
        Single-cell reference with raw counts in .X
    cell_type_col : str
        Column in adata.obs with cell-type labels
    n_samples : int
        Number of pseudobulk samples to generate
    n_cells_per_sample : int
        Number of cells to sample per pseudobulk
    seed : int
        Random seed

    Returns
    -------
    bulk_df : pd.DataFrame
        Pseudobulk expression matrix (samples x genes)
    props_df : pd.DataFrame
        True cell-type proportions (samples x cell types)
    """
    rng = np.random.default_rng(seed)
    cell_types = adata.obs[cell_type_col].unique()

    bulk_profiles = []
    true_proportions = []

    for i in range(n_samples):
        # Random proportions from Dirichlet distribution
        props = rng.dirichlet(np.ones(len(cell_types)))
        n_per_type = np.round(props * n_cells_per_sample).astype(int)

        sampled_indices = []
        for ct, n in zip(cell_types, n_per_type):
            ct_indices = np.where(adata.obs[cell_type_col] == ct)[0]
            if len(ct_indices) > 0 and n > 0:
                sampled = rng.choice(ct_indices, size=min(n, len(ct_indices)),
                                     replace=True)
                sampled_indices.extend(sampled)

        # Sum expression across sampled cells
        X_subset = adata.X[sampled_indices]
        if issparse(X_subset):
            X_subset = X_subset.toarray()
        bulk_profile = X_subset.sum(axis=0)
        bulk_profiles.append(bulk_profile)

        # Record true proportions
        actual_props = {ct: 0 for ct in cell_types}
        for ct, n in zip(cell_types, n_per_type):
            actual_props[ct] = n
        total = sum(actual_props.values())
        actual_props = {ct: v / total for ct, v in actual_props.items()}
        true_proportions.append(actual_props)

    bulk_df = pd.DataFrame(
        np.array(bulk_profiles),
        columns=adata.var_names,
        index=[f"pseudobulk_{i}" for i in range(n_samples)]
    )
    props_df = pd.DataFrame(true_proportions,
                             index=[f"pseudobulk_{i}" for i in range(n_samples)])

    return bulk_df, props_df
```

---

## 7. Validation

### Correlation with Known Proportions

```python
from scipy.stats import pearsonr, spearmanr
import matplotlib.pyplot as plt

def validate_deconvolution(estimated_props, true_props):
    """
    Compare estimated vs. true cell-type proportions.
    """
    cell_types = estimated_props.columns

    fig, axes = plt.subplots(1, len(cell_types), figsize=(4 * len(cell_types), 4))
    if len(cell_types) == 1:
        axes = [axes]

    for ax, ct in zip(axes, cell_types):
        est = estimated_props[ct].values
        true = true_props[ct].values

        r_pearson, _ = pearsonr(est, true)
        r_spearman, _ = spearmanr(est, true)

        ax.scatter(true, est, alpha=0.6, s=20)
        ax.plot([0, 1], [0, 1], 'r--', alpha=0.5)
        ax.set_xlabel("True Proportion")
        ax.set_ylabel("Estimated Proportion")
        ax.set_title(f"{ct}\nPearson={r_pearson:.3f}, Spearman={r_spearman:.3f}")

    plt.tight_layout()
    plt.savefig("deconvolution_validation.png", dpi=150, bbox_inches='tight')
    plt.show()
```

### Correlation with Measured Cell Counts

When flow cytometry or other independent measurements are available:

```python
# Compare deconvolution estimates with FACS-measured proportions
# facs_proportions: DataFrame with measured proportions (samples x cell types)
# estimated_proportions: DataFrame with deconvolution output (samples x cell types)

for ct in common_cell_types:
    r, p = pearsonr(facs_proportions[ct], estimated_proportions[ct])
    print(f"{ct}: Pearson r = {r:.3f}, p = {p:.2e}")
```

---

## 8. Critical Pitfalls

### Missing Cell Types

> [!CAUTION]
> Missing cell types in the reference atlas degrade deconvolution accuracy for
> ALL estimated cell types, not just the missing ones. The proportions of
> present cell types will be inflated to compensate for the missing fraction.

**Mitigation strategies:**
- Use a comprehensive reference atlas covering all expected cell types
- Include an "unknown" or "other" category if possible
- Validate results against orthogonal measurements (e.g., IHC, FACS)
- Compare results across multiple deconvolution methods

### Cross-Platform Effects

```
Bulk RNA-seq (polyA-selected) + scRNA-seq reference (10x 3' bias)
    -> Systematic bias in gene detection
    -> Use CIBERSORTx batch correction mode
```

### Granularity Mismatch

```
Fine-grained reference (20+ subtypes) + Bulk with few dominant types
    -> Noisy estimates for rare subtypes
    -> Aggregate related subtypes before deconvolution
```

---

## 9. Method Selection Guide

| Scenario | Recommended Method | Rationale |
|----------|-------------------|-----------|
| Multi-subject reference available | MuSiC | Leverages cross-subject variability |
| Cross-platform bulk vs. scRNA | CIBERSORTx | Built-in batch correction |
| Large high-quality reference | Scaden | Learns non-linear relationships |
| Multiple reference datasets | SCDC | Ensemble approach for robustness |
| Quick exploratory analysis | BisqueRNA | Simple, fast, R-native |
| Closely related cell types | Any + AutoGeneS features | Better marker discrimination |

---

## Best Practices Summary

1. **Use raw counts in linear scale**: Never log-transform or z-score normalize inputs for deconvolution.
2. **Invest in feature selection**: Marker gene quality directly determines proportion accuracy. Use AutoGeneS for difficult cell-type distinctions.
3. **Validate with pseudobulk**: Generate pseudobulk samples from held-out single-cell data to benchmark before applying to real bulk data.
4. **Check for missing cell types**: Ensure the single-cell reference covers all cell types present in the bulk tissue.
5. **Compare multiple methods**: No single method wins universally. Run at least two methods and compare concordance.
6. **Use multi-subject references**: Methods like MuSiC perform better when biological variability across subjects is captured in the reference.
7. **Report uncertainty**: Provide confidence intervals or concordance across methods, not just point estimates.

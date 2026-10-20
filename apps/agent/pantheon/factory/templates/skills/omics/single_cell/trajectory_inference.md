---
id: trajectory_inference
name: Cell Trajectory Inference
description: |
  Perform pseudotime analysis and trajectory inference for single-cell data
  using scanpy and scvelo. Useful for studying cell differentiation, lineage
  tracing, and developmental processes like neurogenesis.
tags: [trajectory, pseudotime, velocity, differentiation, lineage]
---

# Cell Trajectory Inference

This skill covers pseudotime analysis and trajectory inference for understanding
cell state transitions, differentiation paths, and developmental processes.

## When to Use

- Studying cell differentiation (e.g., neural progenitor → mature neuron)
- Lineage tracing in developmental or regenerative contexts
- Identifying branching points in cell fate decisions
- Analyzing neurogenesis or other continuous biological processes

## Methods Overview

| Method | Tool | Best For |
|--------|------|----------|
| Diffusion Pseudotime (DPT) | scanpy | General trajectory, robust |
| PAGA | scanpy | Complex topologies, branching |
| RNA Velocity | scvelo | Directional dynamics |
| Monocle3 | R/monocle3 | Complex branching, large datasets |

## Workflow 1: Diffusion Pseudotime (Scanpy)

### Prerequisites

```python
import scanpy as sc
import numpy as np
```

### Step 1: Prepare Data

Ensure data is preprocessed with PCA and neighbors computed:

```python
# Standard preprocessing (if not done)
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.pp.pca(adata, n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
sc.tl.umap(adata)
```

### Step 2: Compute Diffusion Map

```python
# Compute diffusion map
sc.tl.diffmap(adata, n_comps=15)

# Visualize diffusion components
sc.pl.diffmap(adata, color=['leiden', 'cell_type'], components=['1,2', '2,3'])
```

### Step 3: Set Root Cell

Choose root cell based on biological knowledge (e.g., stem cell cluster):

```python
# Option 1: Set root by cluster (recommended)
# Find cells in the stem/progenitor cluster
root_cluster = 'NSC'  # or cluster number
root_cells = adata.obs[adata.obs['cell_type'] == root_cluster].index

# Set root as the cell with highest stemness score or specific marker
root_idx = np.where(adata.obs_names == root_cells[0])[0][0]
adata.uns['iroot'] = root_idx

# Option 2: Set root by marker gene expression
# Use high expression of stem cell marker (e.g., Sox2, Nes)
marker = 'Sox2'
if marker in adata.var_names:
    root_idx = np.argmax(adata[:, marker].X.toarray().flatten())
    adata.uns['iroot'] = root_idx
```

### Step 4: Compute Pseudotime

```python
# Compute diffusion pseudotime
sc.tl.dpt(adata)

# Visualize pseudotime
sc.pl.umap(adata, color=['dpt_pseudotime', 'cell_type'], cmap='viridis')

# Heatmap of genes along pseudotime
sc.pl.paga_path(
    adata,
    nodes=['NSC', 'IPC', 'Immature_Neuron', 'Mature_Neuron'],
    keys=['Nes', 'Sox2', 'Dcx', 'Prox1', 'Rbfox3']
)
```

> [!TIP]
> For neurogenesis studies, order should typically be:
> NSC (Nes+/Sox2+) → IPC (Tbr2+) → Immature (Dcx+) → Mature (Prox1+/NeuN+)

## Workflow 2: PAGA (Partition-based Graph Abstraction)

PAGA is excellent for complex trajectories with multiple branches:

```python
# Compute PAGA
sc.tl.paga(adata, groups='leiden')

# Plot PAGA graph
sc.pl.paga(adata, color=['leiden', 'cell_type'], edge_width_scale=0.5)

# PAGA-guided UMAP for better trajectory visualization
sc.tl.draw_graph(adata, init_pos='paga')
sc.pl.draw_graph(adata, color=['dpt_pseudotime', 'cell_type'], legend_loc='right margin')
```

## Workflow 3: RNA Velocity (scVelo)

For directional trajectory analysis:

```python
import scvelo as scv

# Load loom file with spliced/unspliced counts
# Or merge with existing adata
ldata = scv.read('velocyto_output.loom')
adata = scv.utils.merge(adata, ldata)

# Preprocess velocity data
scv.pp.filter_and_normalize(adata, min_shared_counts=20, n_top_genes=2000)
scv.pp.moments(adata, n_pcs=30, n_neighbors=30)

# Compute velocity
scv.tl.velocity(adata)
scv.tl.velocity_graph(adata)

# Visualize velocity
scv.pl.velocity_embedding_stream(adata, basis='umap', color='cell_type')
scv.pl.velocity_embedding(adata, basis='umap', arrow_length=3, arrow_size=2)

# Latent time (velocity-based pseudotime)
scv.tl.latent_time(adata)
scv.pl.scatter(adata, color='latent_time', cmap='viridis')
```

> [!WARNING]
> RNA velocity requires spliced/unspliced count matrices from velocyto or loompy.
> If not available, use diffusion pseudotime instead.

## Neurogenesis-Specific Analysis

### Key Markers for Trajectory Ordering

```python
# Neural stem cell to mature neuron markers
neurogenesis_markers = {
    'NSC': ['Nes', 'Gfap', 'Sox2'],           # Neural stem cells
    'Proliferating': ['Mki67', 'Top2a'],       # Proliferation
    'IPC': ['Tbr2', 'Dcx'],                    # Intermediate progenitors
    'Immature_Neuron': ['Dcx', 'Prox1'],       # Immature/migrating
    'Mature_GC': ['Prox1', 'Rbfox3', 'Calb1']  # Mature granule cells
}

# Plot marker expression along pseudotime
genes = ['Sox2', 'Nes', 'Mki67', 'Dcx', 'Prox1', 'Rbfox3']
sc.pl.violin(adata, genes, groupby='cell_type', rotation=45)
```

### Trajectory-based Differential Expression

```python
# Find genes that change along pseudotime
sc.tl.rank_genes_groups(adata, groupby='leiden', method='wilcoxon')

# Alternative: correlation with pseudotime
import pandas as pd
from scipy.stats import spearmanr

pseudotime = adata.obs['dpt_pseudotime'].values
correlations = []
for gene in adata.var_names:
    expr = adata[:, gene].X.toarray().flatten()
    corr, pval = spearmanr(pseudotime, expr)
    correlations.append({'gene': gene, 'correlation': corr, 'pvalue': pval})

corr_df = pd.DataFrame(correlations)
corr_df = corr_df.sort_values('correlation', ascending=False)

# Top genes increasing with pseudotime (maturation markers)
print(corr_df.head(20))
# Top genes decreasing with pseudotime (stemness markers)
print(corr_df.tail(20))
```

## Comparing Trajectories Between Conditions

For comparing control vs treatment groups:

```python
# Subset by condition
adata_ctrl = adata[adata.obs['condition'] == 'Control']
adata_treat = adata[adata.obs['condition'] == 'Treatment']

# Compute pseudotime separately
for ad in [adata_ctrl, adata_treat]:
    sc.tl.diffmap(ad)
    ad.uns['iroot'] = np.argmax(ad[:, 'Sox2'].X.toarray().flatten())
    sc.tl.dpt(ad)

# Compare pseudotime distributions
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.hist(adata_ctrl.obs['dpt_pseudotime'], bins=50, alpha=0.5, label='Control')
ax.hist(adata_treat.obs['dpt_pseudotime'], bins=50, alpha=0.5, label='Treatment')
ax.legend()
ax.set_xlabel('Pseudotime')
ax.set_ylabel('Cell count')
plt.savefig('pseudotime_comparison.pdf')
plt.show()

# Statistical test
from scipy.stats import mannwhitneyu
stat, pval = mannwhitneyu(
    adata_ctrl.obs['dpt_pseudotime'], 
    adata_treat.obs['dpt_pseudotime']
)
print(f"Mann-Whitney U test p-value: {pval:.2e}")
```

## Quality Assessment

> [!IMPORTANT]
> Always validate trajectory results:
> 1. Check that root cell is biologically appropriate
> 2. Verify marker gene ordering matches expected biology
> 3. Compare with known differentiation timelines
> 4. Use multiple methods for robustness

```python
# Validate with known markers
validation_genes = ['Sox2', 'Nes', 'Dcx', 'Prox1']
sc.pl.scatter(
    adata, 
    x='dpt_pseudotime', 
    y=validation_genes,
    color='cell_type',
    save='_pseudotime_validation.pdf'
)
```

## Large Dataset Handling

For datasets > 100k cells:

```python
# Subsample for trajectory computation
sc.pp.subsample(adata, n_obs=50000)

# Or use approximation methods
sc.pp.neighbors(adata, n_neighbors=15, method='rapids')  # GPU acceleration
```

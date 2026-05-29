---
id: spatial_deconvolution
name: Spatial Cell Type Deconvolution (Cell2location / Tangram)
description: |
  Deconvolve spatial transcriptomics spots into cell type abundances using
  Cell2location or Tangram. Cell2location trains a two-stage Bayesian model
  (reference signatures + spatial mapping); Tangram uses optimal transport
  for faster exploratory analysis and gene imputation.
tags: [spatial, deconvolution, cell2location, tangram, cell-type, omicverse]
---

# Spatial Cell Type Deconvolution with Cell2location and Tangram

Estimate per-spot cell type composition from spatial transcriptomics data using
[Cell2location](https://cell2location.readthedocs.io/) (two-stage Bayesian model)
or [Tangram](https://tangram-sc.readthedocs.io/) via
[OmicVerse](https://omicverse.readthedocs.io/). Cell2location provides
quantitative abundance estimates; Tangram is faster and supports gene imputation.

## Prerequisites

> [!WARNING]
> Cell2location trains a spatial model for up to 20,000 epochs. A CUDA-capable
> GPU is **strongly recommended**. CPU training is possible but may take hours
> to days depending on dataset size.

```bash
pip install cell2location scvi-tools omicverse
```

Cell2location uses [scvi-tools](https://scvi-tools.org/) as its backend.
Ensure compatible versions are installed together.

## Workflow

### 1. Prepare scRNA-seq Reference

Cell2location learns cell type gene expression signatures from a scRNA-seq
reference. The reference **must contain raw integer counts** (not normalized
or log-transformed).

```python
import scanpy as sc
import cell2location
from cell2location.utils.filtering import filter_genes

adata_ref = sc.read_h5ad("reference_scrna.h5ad")

# Must use RAW COUNTS (not normalized)
# If log-normalized, recover counts first:
#   adata_ref.X = adata_ref.raw.X.copy()

# Filter genes — remove lowly expressed genes that add noise
selected = filter_genes(
    adata_ref,
    cell_count_cutoff=5,
    cell_percentage_cutoff2=0.03,
    nonz_mean_cutoff=1.05,
)
adata_ref = adata_ref[:, selected].copy()
```

### 2. Train Reference Model (RegressionModel)

Extract cell type gene expression signatures from the scRNA-seq reference.
This stage is fast (~200 epochs).

```python
cell2location.models.RegressionModel.setup_anndata(
    adata_ref, batch_key="batch", labels_key="celltype"
)
mod_ref = cell2location.models.RegressionModel(adata_ref)
mod_ref.train(max_epochs=200, batch_size=2500, train_size=1, lr=0.002)

# Always check convergence — loss should plateau
mod_ref.plot_history(20)

# Export estimated signatures
adata_ref = mod_ref.export_posterior(
    adata_ref, sample_kwargs={"num_samples": 1000, "batch_size": 2500}
)
inf_aver = adata_ref.varm["means_per_cluster_mu_fg"]
```

### 3. Train Spatial Model (Cell2location)

Map the learned signatures to spatial locations. This is the main computational
bottleneck — the spatial model trains for up to 20,000 epochs.

```python
# Filter spatial data to genes shared with reference signatures
intersect = adata_sp.var_names.intersection(inf_aver.index)
adata_sp = adata_sp[:, intersect].copy()
inf_aver = inf_aver.loc[intersect, :]

cell2location.models.Cell2location.setup_anndata(
    adata_sp, batch_key="batch"
)
mod_sp = cell2location.models.Cell2location(
    adata_sp,
    cell_state_df=inf_aver,
    N_cells_per_location=10,    # estimate from histology
    detection_alpha=20,
)
mod_sp.train(max_epochs=20000, batch_size=None, train_size=1)

# Check convergence — loss should plateau well before 20k epochs
mod_sp.plot_history(1000)
```

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `max_epochs` (ref) | 200 | Reference model training epochs |
| `max_epochs` (spatial) | 20000 | Spatial model training epochs -- main bottleneck |
| `batch_size` (ref) | 2500 | Mini-batch size for reference model |
| `batch_size` (spatial) | `None` | Full batch recommended for spatial model |
| `N_cells_per_location` | 10 | Expected cells per spot -- estimate from histology |
| `detection_alpha` | 20 | Regularization for detection sensitivity |
| `lr` | 0.002 | Learning rate for reference model |

### 4. Extract Results

Export posterior estimates and convert absolute abundances to proportions.

```python
adata_sp = mod_sp.export_posterior(
    adata_sp, sample_kwargs={"num_samples": 1000, "batch_size": 2500}
)

# Cell type abundances (5th percentile, conservative estimate)
abund = adata_sp.obsm['q05_cell_abundance_w_sf']

# Convert to proportions (fractions summing to 1 per spot)
props = abund.div(abund.sum(axis=1).clip(lower=1e-9), axis=0)
adata_sp.obsm['prop_celltypes'] = props

# Store per-celltype columns in obs for easy plotting
for ct in props.columns:
    adata_sp.obs[f'prop_{ct}'] = props[ct].values
```

### 5. Tangram Alternative

> [!TIP]
> Cell2location is more accurate for quantitative abundance estimation; Tangram
> is better for gene imputation and faster for exploratory analysis. Consider
> running both and comparing results.

Tangram uses optimal transport to map cells to spatial locations. It does not
require two-stage training and is simpler to set up.

```python
import omicverse as ov

decov = ov.space.Deconvolution(
    adata_sc=adata_ref.copy(),
    adata_sp=adata_sp.copy(),
)
decov.preprocess_sc(mode='shiftlog|pearson', n_HVGs=3000, target_sum=1e4)
decov.preprocess_sp(mode='pearsonr', n_svgs=3000, target_sum=1e4)

decov.deconvolution(
    method='Tangram',
    celltype_key_sc='celltype',
    tangram_kwargs={
        'mode': 'cells',
        'num_epochs': 500,
        'device': 'cuda:0',
    },
)

# Gene imputation via Tangram mapping
decov.tangram_inference()
decov.impute(method='Tangram')
```

### 6. Visualize Cell Type Proportions

#### 2D Spatial Plot

```python
import scanpy as sc
import matplotlib.pyplot as plt

# Plot individual cell type proportions on spatial coordinates
celltypes_to_plot = props.columns[:6]  # top 6 cell types
fig, axes = plt.subplots(2, 3, figsize=(18, 12))
for ax, ct in zip(axes.flat, celltypes_to_plot):
    sc.pl.spatial(
        adata_sp,
        color=f'prop_{ct}',
        spot_size=1,
        cmap='magma',
        title=ct,
        ax=ax,
        show=False,
    )
plt.tight_layout()
plt.savefig("celltype_proportions_spatial.png", dpi=200, bbox_inches="tight")
```

#### 3D Spatial Plot (PyVista)

For 3D spatial data, use PyVista to visualize cell type proportions with depth:

```python
import numpy as np
import pyvista as pv

coords = adata_sp.obsm['spatial'].copy()
if coords.shape[1] == 3:
    coords[:, -1] = -coords[:, -1]  # flip z for visual convention

ct_name = "Cardiomyocyte"  # cell type to visualize
values = adata_sp.obs[f'prop_{ct_name}'].values

cloud = pv.PolyData(coords)
cloud[ct_name] = values

plotter = pv.Plotter(off_screen=True, window_size=[1200, 900])
plotter.set_background('black')
plotter.add_points(
    cloud,
    scalars=ct_name,
    cmap='magma',
    point_size=2,
    opacity=0.3,
    clim=[0, values.quantile(0.95) if hasattr(values, 'quantile') else np.quantile(values, 0.95)],
    scalar_bar_args={"title": ct_name, "color": "white"},
)
plotter.add_text(ct_name, font_size=18, color='white', position='upper_left')
plotter.camera.Elevation(-15)
plotter.camera.Azimuth(-60)
plotter.screenshot(f"deconv_{ct_name}_3d.png")
plotter.close()
```

## Common Pitfalls

1. **Raw counts required**: Cell2location needs raw integer counts in `.X`.
   Normalized or log-transformed data will fail silently and produce unreliable
   results. If your reference is log-normalized, recover counts from `.raw.X`
   or `.layers['counts']` before training.
2. **N_cells_per_location**: Over- or underestimating the expected number of
   cells per spot degrades deconvolution accuracy. Estimate from histology
   images or set to the expected number of cells per spatial bin/spot.
3. **Training convergence**: Always check `mod.plot_history()` after training.
   The reference model should converge within ~200 epochs; the spatial model
   typically needs ~20,000 epochs. If the loss is still decreasing, increase
   `max_epochs`.
4. **GPU memory**: The spatial model on large datasets (>100k spots) may run
   out of GPU memory. Reduce `batch_size` from `None` to a fixed value (e.g.,
   2500) or subsample spots.
5. **Gene intersection**: The spatial and reference datasets must share genes.
   Always filter both to their intersection before training the spatial model.
   A small intersection (<2000 genes) may produce poor results.
6. **Batch effects**: Use `batch_key` when the reference contains multiple
   batches or donors. Cell2location explicitly models batch-specific detection
   sensitivity, which improves signature estimation.
7. **Tangram lowercases gene names**: `tangram.pp_adatas()` converts all
   `var_names` to lowercase in-place (e.g., `Pou5f1` → `pou5f1`). This
   causes downstream gene lookups to fail silently. After Tangram runs,
   restore original casing from the reference:
   ```python
   case_map = {g.lower(): g for g in adata_ref.var_names}
   adata_imputed.var_names = [case_map.get(g, g) for g in adata_imputed.var_names]
   ```
8. **Reference with Ensembl IDs in .raw**: Some references store raw counts
   in `.raw.X` with Ensembl gene IDs while `.X` uses gene symbols. Since
   spatial data uses gene symbols, you may need to recover counts from the
   symbol-space `.X` via `np.expm1()` if it was log1p-normalized, rather
   than using `.raw.X` with mismatched IDs.

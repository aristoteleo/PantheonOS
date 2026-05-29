---
id: spatial_3d_alignment
name: Spatial 3D Slice Alignment (Spateo)
description: |
  Align serial spatial transcriptomics sections into a 3D volume using
  Spateo morpho_align. Pairwise rigid registration based on morphology
  and gene expression, with chain transformation accumulation.
tags: [spatial, 3d, alignment, registration, spateo, morpho_align]
---

# Spatial 3D Slice Alignment with Spateo

Align serial tissue sections from spatial transcriptomics experiments into a
coherent 3D volume using [Spateo](https://spateo-release.readthedocs.io/)'s
`morpho_align`. This method performs pairwise rigid registration using both
spatial morphology and gene expression similarity.

## Prerequisites

> [!WARNING]
> Spateo requires `numpy<2`. Use the same dedicated virtual environment as
> for spatial CCI analysis.

```bash
python -m venv .venv-spateo
source .venv-spateo/bin/activate
pip install "numpy<2" spateo-release scanpy
```

## Workflow

### 1. Load Data and Split into Slices

Serial sections need a grouping variable (z-height, section ID, or puck ID)
to split into individual slices.

```python
import anndata as ad
import numpy as np
import scanpy as sc
import spateo as st

adata = ad.read_h5ad("spatial_data.h5ad")

# Split by z-coordinate (if 3D coords exist)
coords = adata.obsm['spatial_3D']
z_round = np.round(coords[:, 2], decimals=3)
adata.obs["z_height"] = z_round
adata.obs["z_height"] = adata.obs["z_height"].astype("category")

slices = [
    adata[adata.obs['z_height'] == z].copy()
    for z in adata.obs['z_height'].cat.categories
]
print(f"Created {len(slices)} slices")

# Alternative: split by section/puck_id column
# slices = [adata[adata.obs['section'] == s].copy()
#           for s in adata.obs['section'].cat.categories]
```

### 2. Prepare 2D Spatial Coordinates

`morpho_align` works on 2D coordinates. Extract the XY plane from 3D coords,
or use existing 2D spatial coordinates.

```python
spatial_2d_key = "spatial_2D"
for s in slices:
    s.obsm[spatial_2d_key] = s.obsm['spatial_3D'][:, :2].copy()
```

### 3. Compute PCA Representation

`morpho_align` uses PCA embeddings to match cells by expression similarity.
If `X_pca` is not already in the data, compute it per slice.

```python
for i, s in enumerate(slices):
    if 'X_pca' not in s.obsm:
        s_proc = s.copy()
        sc.pp.normalize_total(s_proc, target_sum=1e4)
        sc.pp.log1p(s_proc)
        sc.pp.highly_variable_genes(s_proc, n_top_genes=2000)
        sc.pp.pca(s_proc, n_comps=30)
        s.obsm['X_pca'] = s_proc.obsm['X_pca'].copy()
```

### 4. Run Pairwise Alignment

Align consecutive slice pairs sequentially. Each call returns aligned
coordinates, from which we extract a rigid transformation (R, t).

```python
Rs = []
Ts = []
key_added = "spatial_aligned"

for i in range(len(slices) - 1):
    print(f"Aligning slice {i} -> {i+1}...")
    align_models, _ = st.align.morpho_align(
        models=[slices[i], slices[i+1]],
        spatial_key=spatial_2d_key,
        key_added=key_added,
        max_iter=200,
        device='cpu',           # 'cpu' or GPU index e.g. '0'
        partial_robust_level=10,
        verbose=False,
        batch_size=2000,
        iter_key_added=None,
        rep_layer='X_pca',
        rep_field='obsm',
        dissimilarity='cos',
        nn_init=False,
    )

    R, t = st.align.solve_RT_by_correspondence(
        align_models[1].obsm[key_added],
        align_models[1].obsm[spatial_2d_key]
    )
    Rs.append(R)
    Ts.append(t)

# Save transformations
np.save("pairwise_rigid_transformation.npy", {'Rs': Rs, 'Ts': Ts})
```

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `max_iter` | `200` | Maximum optimization iterations |
| `device` | `'cpu'` | `'cpu'` or GPU index string (e.g. `'0'`) |
| `partial_robust_level` | `10` | Robustness to partial overlap (higher = more robust) |
| `batch_size` | `2000` | Cells per batch for distance computation |
| `rep_layer` | `'X_pca'` | Expression representation for cell matching |
| `rep_field` | `'obsm'` | Where to find `rep_layer` (`'obsm'` or `'layers'`) |
| `dissimilarity` | `'cos'` | Distance metric: `'cos'` (cosine) or `'kl'` |

**Runtime estimate:** ~15-25 seconds per slice pair on CPU for ~5k cells/slice.

### 5. Apply Chain Transformations

Accumulate pairwise transforms to get global coordinates. The first slice
stays fixed; each subsequent slice is transformed relative to it.

```python
aligned_slices = [s.copy() for s in slices]
aligned_slices[0].obsm[key_added] = aligned_slices[0].obsm[spatial_2d_key].copy()

cur_R = np.eye(2)
cur_t = np.zeros(2)
for i in range(len(Rs)):
    cur_t = Ts[i] @ cur_R.T + cur_t
    cur_R = cur_R @ Rs[i]
    aligned_slices[i+1].obsm[key_added] = (
        aligned_slices[i+1].obsm[spatial_2d_key].copy() @ cur_R.T + cur_t
    )
```

### 6. Construct 3D Coordinates

Add z-coordinates back to create a full 3D aligned dataset.

```python
z_values = sorted(adata.obs['z_height'].unique())

for i, s in enumerate(aligned_slices):
    xy = s.obsm[key_added]
    z = np.full((s.n_obs, 1), z_values[i])
    s.obsm['spatial_3D_aligned'] = np.hstack([xy, z])

adata_3d = ad.concat(aligned_slices, join='inner')
```

### 7. Visualize Before/After

#### 2D overlay (Spateo)

```python
import matplotlib.pyplot as plt

# Before
st.pl.overlay_slices_2d(
    slices=slices,
    spatial_key=spatial_2d_key,
    height=3, ncols=4, cmap='tab20',
)
plt.savefig("before_alignment.png", dpi=150, bbox_inches="tight")

# After
st.pl.overlay_slices_2d(
    slices=aligned_slices,
    spatial_key=key_added,
    height=3, ncols=4, cmap='tab20',
)
plt.savefig("after_alignment.png", dpi=150, bbox_inches="tight")
```

#### 3D visualization (PyVista)

```python
import pyvista as pv

coords = adata_3d.obsm['spatial_3D_aligned']
cloud = pv.PolyData(coords)
cloud['section'] = adata_3d.obs['z_height'].astype(float).values

plotter = pv.Plotter()
plotter.add_points(cloud, scalars='section', cmap='tab10',
                   point_size=2, opacity=0.3)
plotter.set_background('black')
plotter.camera.Elevation(-15)
plotter.camera.Azimuth(-60)
plotter.show()
```

## Loading Saved Transformations

```python
transforms = np.load("pairwise_rigid_transformation.npy", allow_pickle=True).item()
Rs, Ts = transforms['Rs'], transforms['Ts']
```

## Common Pitfalls

1. **NumPy 2.x incompatibility**: Spateo requires `numpy<2`. Use a dedicated
   virtual environment.
2. **Missing `X_pca`**: `morpho_align` needs PCA embeddings. If not in data,
   compute per-slice (normalize → HVG → PCA) before alignment.
3. **2D not 3D coordinates**: `morpho_align` operates on 2D. Extract XY from
   3D coords. Re-attach z-values after alignment.
4. **Transformation order matters**: Chain accumulation must follow the formula
   `cur_t = Ts[i] @ cur_R.T + cur_t; cur_R = cur_R @ Rs[i]`. Reversing the
   order produces incorrect global alignment.
5. **Large slices**: For >10k cells/slice, increase `batch_size` or use GPU
   (`device='0'`) for faster alignment.

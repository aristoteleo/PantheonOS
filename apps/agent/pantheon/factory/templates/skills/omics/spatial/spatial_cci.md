---
id: spatial_cci
name: Spatial Cell-Cell Interaction (Spateo LR)
description: |
  Infer ligand-receptor interactions between spatially adjacent cell types
  using Spateo. Includes spatial neighbor graph construction, two-group
  LR co-expression analysis, and visualization.
tags: [spatial, cci, ligand-receptor, spateo, cell-communication]
---

# Spatial Cell-Cell Interaction with Spateo

Infer ligand-receptor (LR) interactions between spatially neighboring cell types
using [Spateo](https://spateo-release.readthedocs.io/). Unlike non-spatial methods
(LIANA, CellPhoneDB), Spateo only considers cells that are physically adjacent,
reducing false positives from globally co-expressed but spatially distant pairs.

## Prerequisites

> [!WARNING]
> Spateo requires `numpy<2` and may conflict with other packages in your
> environment. **Create a dedicated virtual environment** for spatial CCI analysis.

```bash
python -m venv .venv-spateo
source .venv-spateo/bin/activate
pip install "numpy<2" spateo-release scanpy
```

### LR Database Setup

The pip-installed spateo does **NOT** include the LR database files. You must
download them manually from the spateo source repository:

```bash
# Download the database directory from spateo GitHub
git clone --depth 1 https://github.com/aristoteleo/spateo-release.git /tmp/spateo-src
cp -r /tmp/spateo-src/spateo/tools/database/ ./spateo_lr_db/
rm -rf /tmp/spateo-src
```

The database contains LR pair lists for human, mouse, zebrafish, drosophila,
and axolotl, plus receptor-TF and TF-target mappings.

You **must** pass the `path=` parameter to `find_cci_two_group()` pointing to
this directory. Spateo will NOT find it automatically.

## Workflow

### 1. Load Data and Subsample

Spatial CCI with permutation testing is computationally expensive. Subsample
to a manageable size based on your available compute:

| Cells | num=1000 perms | num=10 perms |
|---|---|---|
| 10k | ~1.2 hours | ~45 seconds |
| 50k | ~45 hours | ~30 minutes |
| 500k | infeasible | ~5 hours |

```python
import spateo as st
import scanpy as sc
import numpy as np

adata = sc.read_h5ad("imputed_spatial.h5ad")

# Subsample if needed
n_subsample = 10000
if adata.n_obs > n_subsample:
    rng = np.random.default_rng(42)
    idx = rng.choice(adata.n_obs, size=n_subsample, replace=False)
    adata = adata[np.sort(idx)].copy()
```

### 2. Prepare Spatial Coordinates

Check which obsm key has spatial coordinates. For 3D analysis, use 3D
coordinates (e.g., `X_spateo_update`), not 2D projections.

```python
# Find and set the correct spatial key
spatial_key = "spatial"
for k in ["X_spateo_update", "X_spateo", "X_spatial"]:
    if k in adata.obsm and adata.obsm[k].shape[1] >= 2:
        spatial_key = k
        break

# Spateo expects coordinates in obsm['spatial']
adata.obsm['spatial'] = adata.obsm[spatial_key].copy()
print(f"Spatial coords: {spatial_key}, shape: {adata.obsm['spatial'].shape}")
```

### 3. Set Required Metadata

```python
# Spateo requires this — will error without it
adata.uns["__type"] = "UMI"
```

### 4. Build Spatial Neighbor Graph

```python
group_key = "mapped_coarse_celltype"  # or your cell type column

_, adata = st.tl.neighbors(
    adata,
    basis="spatial",
    spatial_key="spatial",
    n_neighbors=10,
)

# st.tl.neighbors writes 'spatial_connectivities', but plot_connections
# and other functions read 'connectivities' — must copy manually
adata.obsp["connectivities"] = adata.obsp["spatial_connectivities"].copy()
```

### 5. Visualize Cell Type Spatial Connectivity

```python
import matplotlib.pyplot as plt

fig = st.pl.plot_connections(
    adata,
    cat_key=group_key,
    save_show_or_return="return",
)
plt.savefig("celltype_spatial_connections.png", dpi=200, bbox_inches="tight")
```

### 6. Apply Expression Threshold

Filter out low-level background expression before LR analysis:

```python
threshold = 0.5
X_thresh = adata.X.copy()
X_thresh[X_thresh < threshold] = 0
adata.layers['thresh'] = X_thresh
```

### 7. Run Two-Group LR Analysis

```python
sender_ct = "VCM"
receiver_ct = "FB"
db_dir = "./spateo_lr_db/"  # path to downloaded database

# Prepare cell pair visualization
st.tl.prepare_cci_cellpair_adata(
    adata,
    sender_group=sender_ct,
    receiver_group=receiver_ct,
    group=group_key,
    all_cell_pair=True,
)

# Run LR inference
res = st.tl.find_cci_two_group(
    adata,
    path=db_dir,
    species="human",
    group=group_key,
    sender_group=sender_ct,
    receiver_group=receiver_ct,
    filter_lr="outer",
    layer='thresh',
    min_pairs=0,
    min_pairs_ratio=0,
    top=20,
    num=10,       # permutations — default 1000 is very slow, start with 10
)
```

**Key parameters for `find_cci_two_group()`:**

| Parameter | Default | Description |
|---|---|---|
| `path` | (required) | Path to spateo LR database directory |
| `species` | `"human"` | Species for LR database lookup |
| `num` | `1000` | Number of permutations for significance testing. **This is the main runtime bottleneck.** Start with 10 for exploration, increase for publication. |
| `top` | `20` | Number of top ligands/receptors to consider |
| `filter_lr` | `"outer"` | How to filter LR pairs: `"outer"` (union) or `"inner"` (intersection) |
| `layer` | `None` | Which layer to use for expression (use `'thresh'` for thresholded) |
| `min_pairs` | `5` | Minimum number of co-expressing cell pairs |
| `min_pairs_ratio` | `0.01` | Minimum ratio of co-expressing pairs |
| `pvalue` | `0.05` | Significance threshold |

### 8. Inspect Results

```python
if res is not None:
    lr_df = res["lr_pair"]
    # Significant pairs
    sig = lr_df[lr_df["is_significant"] == True]
    print(f"Significant LR pairs: {len(sig)}/{len(lr_df)}")
    # Top pairs by co-expression ratio
    print(lr_df.sort_values("lr_co_exp_ratio", ascending=False).head(10))
```

### 9. Visualize Sender/Receiver Spatial Distribution

#### 2D spatial plot (via Spateo)

```python
# All sender/receiver cells
st.tl.prepare_cci_cellpair_adata(
    adata, sender_group=sender_ct, receiver_group=receiver_ct,
    group=group_key, all_cell_pair=True,
)
st.pl.space(
    adata, color=["spec"], pointsize=0.8,
    color_key={"other": "#D3D3D3", sender_ct: "red", receiver_ct: "blue"},
    show_legend="upper left", figsize=(5, 5),
    save_show_or_return="show",
)

# Only cells involved in significant LR pairs
st.tl.prepare_cci_cellpair_adata(
    adata, sender_group=sender_ct, receiver_group=receiver_ct,
    cci_dict=res, all_cell_pair=False,
)
st.pl.space(
    adata, color=["spec"], pointsize=0.8,
    color_key={"other": "#D3D3D3", sender_ct: "red", receiver_ct: "blue"},
    show_legend="upper left", figsize=(5, 5),
    save_show_or_return="show",
)
```

#### 3D spatial plot (via PyVista)

For 3D data, use PyVista to show the spatial distribution of sender/receiver
cells with depth context:

```python
import pyvista as pv

coords = adata.obsm['spatial'].copy()
if coords.shape[1] == 3:
    coords[:, -1] = -coords[:, -1]  # flip z for visual convention

labels = adata.obs[group_key].values

plotter = pv.Plotter(off_screen=True, window_size=[1200, 900])
plotter.set_background('black')

# Other cells (transparent background)
mask_other = (labels != sender_ct) & (labels != receiver_ct)
if mask_other.any():
    plotter.add_points(pv.PolyData(coords[mask_other]),
                       color='#555555', point_size=1, opacity=0.05)

# Sender cells
mask_s = (labels == sender_ct)
if mask_s.any():
    plotter.add_points(pv.PolyData(coords[mask_s]),
                       color='red', point_size=2, opacity=0.3, label=sender_ct)

# Receiver cells
mask_r = (labels == receiver_ct)
if mask_r.any():
    plotter.add_points(pv.PolyData(coords[mask_r]),
                       color='#3399FF', point_size=2, opacity=0.3, label=receiver_ct)

plotter.add_text(f"{sender_ct} -> {receiver_ct}", font_size=16,
                 color='white', position='upper_left')
plotter.add_legend(bcolor=(0.1, 0.1, 0.1))
plotter.camera.Elevation(-15)
plotter.camera.Azimuth(-60)
plotter.screenshot("sender_receiver_3d.png")
plotter.close()
```

### 10. Visualize LR Heatmap

```python
import seaborn as sns
import matplotlib.pyplot as plt

lr_df = res["lr_pair"]
df = lr_df[lr_df["lr_co_exp_num"] > 0].sort_values("lr_co_exp_ratio", ascending=False).head(10)

heat = df[["from", "to", "lr_co_exp_ratio"]].pivot(
    index="from", columns="to", values="lr_co_exp_ratio"
).fillna(0)

fig, ax = plt.subplots(figsize=(7, 6))
sns.heatmap(heat, cmap="winter", square=True, linewidths=0.3, ax=ax)
ax.set_xlabel(f"Receptor in {receiver_ct}")
ax.set_ylabel(f"Ligand in {sender_ct}")
plt.tight_layout()
plt.savefig("lr_heatmap.png", dpi=200)
```

### 10. Loop Over Multiple Cell Type Pairs

```python
import pandas as pd

celltypes = adata.obs[group_key].value_counts().index.tolist()[:8]
all_results = {}

for s in celltypes:
    for r in celltypes:
        if s == r:
            continue
        pair = f"{s}-{r}"
        print(f"Running: {pair}")
        tmp = st.tl.find_cci_two_group(
            adata,
            path=db_dir,
            species="human",
            group=group_key,
            sender_group=s,
            receiver_group=r,
            filter_lr="outer",
            layer='thresh',
            min_pairs=0,
            min_pairs_ratio=0,
            top=20,
            num=10,
        )
        if tmp is not None:
            lr = tmp["lr_pair"].sort_values("lr_co_exp_ratio", ascending=False).head(3)
            lr["sr_pair"] = pair
            all_results[pair] = lr

if all_results:
    merged = pd.concat(all_results.values())
    merged.to_csv("all_lr_pairs.csv", index=False)
```

## Common Pitfalls

1. **LR database not found**: pip-installed spateo does not include the database.
   You must download it separately and pass `path=` explicitly.
2. **`spatial_connectivities` vs `connectivities`**: `st.tl.neighbors()` writes
   `spatial_connectivities` but other functions read `connectivities`. Always
   copy: `adata.obsp["connectivities"] = adata.obsp["spatial_connectivities"].copy()`.
3. **Missing `adata.uns["__type"]`**: Must be set to `"UMI"` before running CCI.
4. **2D vs 3D coordinates**: Check `adata.obsm['spatial'].shape[1]`. For 3D
   analysis, use 3D coordinate keys like `X_spateo_update`.
5. **`num` parameter controls permutations**: Default is 1000 which is extremely
   slow for >10k cells. Start with `num=10` for exploration.
6. **NumPy 2.x incompatibility**: Spateo requires `numpy<2`. Use a dedicated
   virtual environment to avoid conflicts with other packages.

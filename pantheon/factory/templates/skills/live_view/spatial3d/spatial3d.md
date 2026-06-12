---
id: spatial3d_live_view
name: 3D Spatial Transcriptomics LiveView
description: |
  Open and drive a 3D point-cloud viewer for single-cell spatial data in the
  Pantheon sidebar — every cell a point at its (x,y,z), coloured by a
  categorical obs column (cell type / cluster) or by a gene's expression, in
  3D / 2D / UMAP. The user's input is usually an AnnData (.h5ad) with 3D
  obsm['spatial']; the recipe converts it to a spatial Zarr. Scales to ~1M+
  cells. view_type="spatial3d".
tags: [live-view, visualization, spatial, transcriptomics, 3d, single-cell, anndata, h5ad, stereo-seq, merfish]
---

# spatial3d — 3D spatial-transcriptomics cell viewer

A GPU point-cloud viewer for **single-cell spatial data**: every cell is a point
at its `(x, y, z)` location, coloured by a **categorical obs column** (cell type
/ cluster) or by a **gene's expression**. Renders in **3D** (orbit), **2D** (flat
section), or **UMAP**. Ported from the Virtual Embryo atlas (`SpatialCellViewer`),
built on deck.gl's `PointCloudLayer` + zarrita; scales to ~1M+ cells.

Use it for 3D spatial transcriptomics (Stereo-seq / MERFISH / Slide-seq / Xenium
reconstructions, or any AnnData with 3D `obsm['spatial']`). For a dense 3D
*scalar volume* (OPT / light-sheet / CT) use `volume3d`; for 2D multiplex images
use `viv` / `vitessce`.

## Data prep — AnnData → a spatial Zarr

The viewer reads a Zarr store with this layout (positions come from
`obsm/spatial`, **not** a LAZ file — so a plain AnnData is all you need):

```
<name>.spatial.zarr/
  obsm/spatial        n_cells × {2,3} float32   ← cell positions (required)
  obs/cell_type       int32 codes + .attrs.categories  ← clusters (required)
  obsm/X_umap         n_cells × 2 float32        ← optional (enables UMAP view)
  X_csc/{data,indices,indptr}  CSC sparse        ← optional (enables gene colouring)
  gene_symbols.json   ["GeneA", ...]             ← optional, aligned with X columns
  _spatial.json       manifest
```

```python
import json, numpy as np, zarr
from pathlib import Path
from scipy.sparse import csc_matrix
from zarr.storage import LocalStore            # zarr 3.x  (zarr 2.x: zarr.DirectoryStore)

def write_spatial_zarr(adata, out_dir, spatial_key=None, cluster_key=None):
    """AnnData → spatial Zarr the spatial3d viewer reads. Auto-detects a 3D
    spatial embedding and the cell-type column; pass spatial_key / cluster_key
    to override. Writes Zarr v2 (broadest zarrita compatibility)."""
    N = adata.n_obs
    # auto-detect the spatial embedding — prefer a 3D one
    if spatial_key is None:
        cand = [k for k in adata.obsm if k.lower() in
                ("spatial", "spatial_3d", "x_spatial", "spatial3d", "x_spatial_3d")]
        three = [k for k in cand if np.asarray(adata.obsm[k]).shape[1] >= 3]
        spatial_key = (three or cand or [list(adata.obsm)[0]])[0]
    coords = np.asarray(adata.obsm[spatial_key], dtype="float32")     # N×2 or N×3
    ndim = coords.shape[1]
    # auto-detect the cluster / cell-type column — first categorical obs
    if cluster_key is None:
        for c in adata.obs.columns:
            if str(adata.obs[c].dtype) in ("category", "object"):
                cluster_key = c; break
        cluster_key = cluster_key or adata.obs.columns[0]
    col = adata.obs[cluster_key].astype("category")
    codes = col.cat.codes.to_numpy().astype("int32")                 # -1 = unset
    cats = list(map(str, col.cat.categories))

    store = LocalStore(str(out_dir))                                 # v2: zarr.DirectoryStore(str(out_dir))
    root = zarr.open_group(store=store, mode="w", zarr_format=2)
    def arr(name, data, chunks):
        a = root.create_array(name, shape=data.shape, dtype=data.dtype, chunks=chunks)
        a[...] = data; return a

    arr("obsm/spatial", coords, (N, ndim))
    ct = arr("obs/cell_type", codes, (N,)); ct.attrs["categories"] = cats

    obsm_meta = {"spatial": {"shape": [N, ndim]}}
    if "X_umap" in adata.obsm:
        um = np.asarray(adata.obsm["X_umap"], dtype="float32")[:, :2]
        arr("obsm/X_umap", um, (N, 2)); obsm_meta["X_umap"] = {"shape": [N, 2]}

    has_expr = False
    if adata.n_vars:
        X = csc_matrix(adata.X)                                       # cells × genes, CSC
        arr("X_csc/data",    X.data.astype("float32"),  (min(65536, max(X.data.size, 1)),))
        arr("X_csc/indices", X.indices.astype("int32"), (min(65536, max(X.indices.size, 1)),))
        arr("X_csc/indptr",  X.indptr.astype("int32"),  (X.indptr.size,))
        Path(out_dir, "gene_symbols.json").write_text(json.dumps(list(map(str, adata.var_names))))
        has_expr = True

    Path(out_dir, "_spatial.json").write_text(json.dumps({
        "n_cells": int(N), "n_genes": int(adata.n_vars),
        "default_spatial_key": "spatial", "spatial_ndim": int(ndim),
        "default_color_obs": "cell_type",
        "obs": {"cell_type": {"kind": "categorical", "n_categories": len(cats)}},
        "obsm": obsm_meta, "has_expression": has_expr,
    }))
    return out_dir

import anndata
adata = anndata.read_h5ad("cells.h5ad")          # ← the user's file
# 3D coords should live in adata.obsm["spatial"] (N×3). If you only have a 2D
# section + a z/section axis, stack: adata.obsm["spatial"] = np.c_[xy, section_z].
write_spatial_zarr(adata, "/workspace/cells.spatial.zarr")
```

Serve it (the data server adds CORS + Range + `no-store`, which zarrita needs):

```python
url = serve_local_data("/workspace/cells.spatial.zarr")
```

## Open

```python
open_live_view(
    view_type="spatial3d",
    title="Mouse embryo — Stereo-seq",
    state={"url": url, "mode": "3d", "colorBy": "cluster"},
)
```

## Drive it

```python
live_view_update(view_id, {"colorBy": "gene", "gene": "Sox2", "colormap": "plasma"})
live_view_update(view_id, {"threshold": 1.5})           # hide cells below this expression
live_view_update(view_id, {"colorBy": "cluster", "cluster": "neural tube"})  # focus one cluster
live_view_update(view_id, {"mode": "umap"})             # 3d | 2d | umap
live_view_update(view_id, {"pointSize": 1.6, "opacity": 0.8})
live_view_update(view_id, {"camera": {"rotationOrbit": 120, "rotationX": 25, "zoom": 4}})
live_view_screenshot(view_id)                            # reads the deck.gl WebGL canvas
```

The user can orbit, switch view/colour mode, pick a gene, and click a cluster in
the legend to focus it — those changes **round-trip back to you** in the view
state (mode, colorBy, gene, focused cluster, 3D camera), so a screenshot reflects
what they're looking at.

## State reference

| field | type | default | effect |
|---|---|---|---|
| `url` | string | — | spatial `.zarr` root URL. Change → reload. |
| `spatialKey` | string | `"spatial"` | obsm key for positions. Change → reload. |
| `mode` | `"3d"`\|`"2d"`\|`"umap"` | `"3d"` (`"2d"` if 2D data) | orbit / flat section / UMAP. |
| `colorBy` | `"cluster"`\|`"gene"` | `"cluster"` | colour source. |
| `clusterKey` | string | manifest `default_color_obs` | obs column for cluster colours. |
| `gene` | string | — | gene symbol (colorBy="gene"). |
| `colormap` | string | `"viridis"` | viridis/magma/plasma/inferno/cividis/turbo/blues/reds. |
| `threshold` | number | `0` | hide cells with expression below this. |
| `cluster` | string\|null | null | focus one cluster by NAME, dim the rest. |
| `pointSize` | 0.05..3.5 | `1` | world-space point size (auto-scaled to the dataset). |
| `opacity` | 0..1 | `1` | point opacity. |
| `camera` | object | — | `{rotationOrbit, rotationX, zoom}` (3D); round-trips on orbit. |
| `title` | string | — | header label. |

## Gotchas

- **Positions from `obsm/spatial`** — N×3 for a true 3D cloud; N×2 renders flat
  (z=0). No LAZ needed.
- **Cluster column must be categorical** with `.attrs.categories` on the zarr
  array (the recipe handles this). Codes are int32, `-1` = unassigned (grey).
- **Gene colouring needs the CSC sidecar** (`X_csc/*`) + `gene_symbols.json`
  aligned with the X column order. Each gene is one range-sliced read — cheap
  even at 1M cells. Omit X to ship a positions-only cluster viewer.
- **Zarr v2 recommended** (the recipe writes v2). zarrita reads v2 and v3, but v2
  avoids any v3-codec mismatch.
- **First open downloads the deck.gl bundle (~1.6 MB)** from the CDN, then it's
  cached for the session.
- Needs CORS + HTTP Range; `serve_local_data` / the live-view data server
  provide both.
- Depth & lighting are handled internally (depth-test on in 3D for correct
  occlusion; `material:{unlit:true}` so there's no fake specular streak as you
  orbit). Opacity in 3D reads as a tint, not true see-through (Z-sorting millions
  of points is untenable) — standard for dense ST clouds.

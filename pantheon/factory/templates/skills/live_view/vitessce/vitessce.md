---
id: vitessce_live_view
name: Visualize Data with Vitessce LiveView
description: |
  Open and drive an interactive Vitessce browser (spatial transcriptomics,
  single-cell, microscopy imaging) in the Pantheon sidebar via the
  live_view tools. Covers building a valid view config, the coordination
  model used to drive the view, and how to avoid the common failure of
  invented data URLs.
tags: [vitessce, spatial, single-cell, visium, xenium, ome-zarr, anndata, live-view]
---

# Visualizing Data with Vitessce

Vitessce is an interactive browser for spatial transcriptomics, single-cell,
and microscopy-imaging data. You open it as a LiveView and then drive it.

## How Vitessce works (important)

Vitessce is a **frontend-only** library — it has **no backend server**. It
reads data **directly over HTTP** from the URLs in its *view config*. Data
must be in a chunked format (**Zarr**: AnnData-Zarr, OME-Zarr; or OME-TIFF,
CSV) hosted somewhere the browser can fetch with CORS.

⚠️ **The #1 failure mode: invented data URLs.** Do NOT guess or hand-type
data file URLs — fabricated URLs 404 and the view shows nothing. Only ever
use URLs that are either (a) a published Vitessce example config you
actually retrieved, or (b) produced by the `vitessce` Python package from
data you actually converted.

## The workflow

```
1. Just a public demo? open_live_view("vitessce", title)  ← no state, see below
   Otherwise, get/build a valid Vitessce view config (see below)
2. open_live_view("vitessce", title, state=config)   → view_id
3. verify:    live_view_get_state(view_id)            (status + diagnostics)
4. drive it:  live_view_update(view_id, patch)
5. observe:   live_view_get_state(view_id)            (incl. the user's edits)
```

⚠️ **Verifying Vitessce.** `live_view_screenshot` does NOT work for it —
Vitessce renders via WebGL / deck.gl, which an html2canvas screenshot cannot
capture; a blank image is expected, not evidence of breakage. Verify with
`live_view_get_state`: `status` must be `ready` and `diagnostics` empty.

**`status: error` means Vitessce rejected the config** (a bad coordination
key, an unreachable data URL). Vitessce also loads **asynchronously** — an
invalid config surfaces a few seconds after `open_live_view`, not instantly.
So do the verifying `live_view_get_state` as a **late step**, right before
you report to the user — never trust a check made immediately after open.
If `status` is `error`, fix the config and reopen; do not report success.

For Vitessce the LiveView **state IS the Vitessce view config**. A
`live_view_update` patch is **deep-merged** into the config — almost always
into `coordinationSpace` (see "Driving the view" below).

## Building a config — use the `vitessce` Python package

The reliable way to produce a valid config is the `vitessce` Python package
(run it in `python_interpreter`). It knows the schema and will not typo URLs.

```python
# pip install vitessce  (if missing)
from vitessce import VitessceConfig, AnnDataWrapper, Component as cm

vc = VitessceConfig(schema_version="1.0.16", name="My dataset")
dataset = vc.add_dataset(name="data").add_object(
    AnnDataWrapper(
        # for remote data: adata_url=...   for local: adata_path=... (needs a server)
        adata_url="https://<host>/data.h5ad.zarr",
        obs_embedding_paths=["obsm/X_umap"],
        obs_embedding_names=["UMAP"],
        obs_set_paths=["obs/cell_type"],
        obs_set_names=["Cell Type"],
        obs_feature_matrix_path="X",
    )
)
scatter = vc.add_view(cm.SCATTERPLOT, dataset=dataset, mapping="UMAP")
sets    = vc.add_view(cm.OBS_SETS, dataset=dataset)
genes   = vc.add_view(cm.FEATURE_LIST, dataset=dataset)
heatmap = vc.add_view(cm.HEATMAP, dataset=dataset)
vc.layout((scatter | sets) / (genes | heatmap))
config = vc.to_dict(base_url="https://<host>")   # -> pass this to open_live_view
```

`vc.to_dict()` returns a fully valid config dict. Pass it straight to
`open_live_view("vitessce", title, state=config)`.

## View config structure (for understanding / hand-editing)

```jsonc
{
  "version": "1.0.16",
  "name": "...",
  "initStrategy": "auto",          // "auto" fills in obvious coordinations
  "datasets": [{
    "uid": "ds",
    "files": [{
      "fileType": "anndata.zarr",   // see fileTypes below
      "url": "https://.../data.zarr",
      "options": { /* which obsm/obs/X paths to read */ },
      "coordinationValues": { "obsType": "cell" }
    }]
  }],
  "coordinationSpace": { /* the live state — see Driving the view */ },
  "layout": [{
    "component": "spatial",         // spatial | scatterplot | heatmap |
                                    // obsSets | featureList | layerController |
                                    // description | status
    "x": 0, "y": 0, "w": 6, "h": 12, // 12-column grid
    "coordinationScopes": { "dataset": "ds" }
  }]
}
```

Common `fileType`s: `anndata.zarr`, `obsEmbedding.csv`, `image.ome-zarr`,
`obsSegmentations.json`, `obsFeatureMatrix.anndata.zarr`. (The `vitessce`
package picks these for you.)

## Driving the view — the coordination model

`coordinationSpace` is the live state. Every view is linked to it; changing
a value updates all linked views. Drive it with `live_view_update`, patching
into `coordinationSpace`. Each coordination type is scoped — the default
scope is `"A"`.

```python
# zoom a spatial view
live_view_update(view_id, {"coordinationSpace": {"spatialZoom": {"A": 4}}})

# pan
live_view_update(view_id, {"coordinationSpace": {
    "spatialTargetX": {"A": 1200}, "spatialTargetY": {"A": 900}}})

# color cells by a gene's expression
live_view_update(view_id, {"coordinationSpace": {
    "obsColorEncoding": {"A": "geneSelection"},
    "featureSelection": {"A": ["CD3D"]}}})

# select a cell set
live_view_update(view_id, {"coordinationSpace": {
    "obsSetSelection": {"A": [["Cell Type", "T cell"]]}}})
```

Useful coordination types: `spatialZoom`, `spatialTargetX`, `spatialTargetY`,
`spatialRotation`, `embeddingZoom`, `embeddingTargetX`, `embeddingTargetY`,
`obsColorEncoding` (`"cellSetSelection"` | `"geneSelection"`),
`featureSelection` (list of gene names), `obsSetSelection`, `obsHighlight`.

⚠️ **`coordinationSpace` is strictly validated — do NOT invent key names.**
Vitessce rejects the **entire config** if `coordinationSpace` contains one
unrecognized coordination type (you get "Config validation failed"). Only
use a coordination type you have actually seen in a Vitessce config or in
this list. If unsure a name exists, do not add it — leave `initStrategy:
"auto"` to fill defaults, or change behaviour through the layer-controller
UI instead.

Always `live_view_get_state(view_id)` before the next move — it reflects
changes the **user** made by interacting with the view directly, and its
`status`/`diagnostics` reveal a rejected config.

## Visualizing the user's own data

Vitessce needs the data as Zarr served over HTTP+CORS:

1. Convert with `python_interpreter`: AnnData `adata.write_zarr(path)`;
   images → OME-Zarr (`bioformats2raw` / `ome-zarr-py`).
2. Serve it with CORS so the browser can fetch it, and get a base URL.
   *(If a `serve_local_data` / data-server tool is available, use it; it
   turns a workspace path into a fetchable URL. Until then, only remote /
   public datasets work.)*
3. Build the config with the `vitessce` package, `base_url` = the served URL.

## Advanced: images, segmentations, and multi-modal views

Vitessce is built on Viv, so it **renders images** too — and it adds a
per-cell data model on top, so it shows **cell segmentations as interactive
objects** (hover → cell id, click → select, colour by cell type or gene),
not as flat pixels.

> ⚠️ **Just want to SEE the cell boundaries on an image? Use Viv, not
> Vitessce.** Vitessce's `obsSegmentations` makes cells interactive
> (hover → cell id, colour by data) but it does **not** render clean visible
> boundaries — a segmentation-only overlay shows up as a flat blob. For a
> visual boundary overlay, the `viv` skill (boundaries as an extra channel)
> is simpler and far crisper. Reach for Vitessce `obsSegmentations` **only**
> when per-cell interactivity (click/select cells, colour by cell type or
> gene, link to a UMAP/heatmap) is genuinely needed.

A Cellpose / StarDist mask (`masks.tif`, integer labels, one per cell) is
exactly a segmentation bitmask: convert it to OME-TIFF, then wrap it with
`ObsSegmentationsOmeTiffWrapper`.

### Recipe — image + segmentation + per-cell data

Modern image/segmentation configs use the **`spatialBeta`** and
**`layerControllerBeta`** views (not the older `spatial` / `layerController`).
Build with the `vitessce` package:

```python
from vitessce import (VitessceConfig, ImageOmeTiffWrapper,
    ObsSegmentationsOmeTiffWrapper, AnnDataWrapper)

vc = VitessceConfig(schema_version="1.0.17", name="Tissue + cells")
ds = (vc.add_dataset("tissue")
  # the microscopy image (multichannel OME-TIFF / OME-Zarr)
  .add_object(ImageOmeTiffWrapper(img_url="https://host/image.ome.tif"))
  # the segmentation: a LABEL image — each integer is one cell
  .add_object(ObsSegmentationsOmeTiffWrapper(
      img_url="https://host/masks.ome.tif",
      coordination_values={"obsType": "cell"}))
  # per-cell data, linked to the segmentation by the shared obsType
  .add_object(AnnDataWrapper(
      adata_url="https://host/cells.h5ad.zarr",
      obs_set_paths=["obs/cell_type"], obs_set_names=["Cell Type"],
      obs_feature_matrix_path="X",
      coordination_values={"obsType": "cell"})))

spatial = vc.add_view("spatialBeta", dataset=ds)          # image + segmentation
lc      = vc.add_view("layerControllerBeta", dataset=ds)  # channel / layer UI
sets    = vc.add_view("obsSets", dataset=ds)              # cell-type tree
genes   = vc.add_view("featureList", dataset=ds)          # gene picker
vc.layout((spatial | lc) / (sets | genes))
config = vc.to_dict(base_url="https://host")
```

The shared `coordination_values={"obsType": "cell"}` is what links the
segmentation to the AnnData — without it the cells have no data to colour
by. An image with no segmentation/cells is just the first `.add_object`.

### Colouring segmented cells

Once linked, drive colouring with the same `coordinationSpace` patches:

```python
# colour cells by cell type
live_view_update(view_id, {"coordinationSpace": {
    "obsColorEncoding": {"A": "cellSetSelection"}}})
# colour cells by a gene's expression
live_view_update(view_id, {"coordinationSpace": {
    "obsColorEncoding": {"A": "geneSelection"},
    "featureSelection": {"A": ["EPCAM"]}}})
```

### Making segmentation cells distinguishable

A bitmask segmentation with **no per-cell data** renders as one solid,
filled colour — every cell the same — so you cannot see individual cells or
their boundaries. This is the "all one colour / no boundaries" symptom.

**Fix: give every cell a per-cell value and colour by it** — neighbouring
cells then get different colours, so every boundary shows. A bare Cellpose /
StarDist mask carries no data, so compute one. Cell **area** is the easy,
reliable choice:

```python
import numpy as np, anndata as ad, pandas as pd
from skimage.measure import regionprops_table

mask = np.load("masks.npy")          # integer label image, one label per cell
props = regionprops_table(mask, properties=["label", "area"])
labels = props["label"].astype(str)
adata = ad.AnnData(
    X=np.zeros((len(labels), 1), dtype="float32"),
    obs=pd.DataFrame({
        # binned area → a categorical → each group a distinct colour.
        # (Use `labels` modulo ~12 instead for purely random, maximum
        #  boundary contrast when an informative colouring is not needed.)
        "area_bin": pd.qcut(props["area"], 8, labels=False, duplicates="drop")
                      .astype(str),
    }, index=labels),       # obs index MUST equal the bitmask label values
)
adata.write_zarr("cells.h5ad.zarr")
```

The `obs` index must equal the bitmask label values so Vitessce links each
row to its cell. Add `AnnDataWrapper(adata_url=…,
obs_set_paths=["obs/area_bin"], obs_set_names=["Area"])` to the same
dataset, linked by `coordination_values={"obsType": "cell"}`, and set
`coordinationSpace.obsColorEncoding` to `"cellSetSelection"`. Each cell
group gets its own colour — boundaries pop.

**Outline mode is unreliable to hand-set.** `spatialSegmentationFilled` is a
real coordination type, but in a `spatialBeta` config it only takes effect
when wired to the segmentation layer through the beta `coordinationScopesBy`
machinery — a flat `coordinationSpace` entry is silently ignored and the
view stays filled. Do not rely on it from an agent-built config; colour-by-
data above is the dependable way. (The user can still toggle fill in the
`layerControllerBeta` panel interactively.)

### Image-only: Vitessce or the Viv plugin?

For a *pure image* with channel controls and nothing else, the `viv` viewer
is lighter. Reach for Vitessce when the image comes **with** cells,
segmentation, embeddings, or expression you want coordinated in one view.

## Quick public-data demo — built in, do not search

When the user just wants to *see a demo* ("打开一个公开空间转录组 demo"),
there is a **bundled, verified** public config. Open it with **no `state`**:

```
open_live_view(view_type="vitessce", title="Spatial Transcriptomics Demo")
```

The viewer auto-loads its bundled `demo.json` (Human lymph node 10x Visium,
public data). **Do NOT web-search for a config, build one, or delegate that
to a sub-agent** — the demo is built in. Only build a config yourself when
the user wants their *own* data (see above).

## Checklist before open_live_view

- [ ] config came from the `vitessce` package OR a retrieved published config
- [ ] every `files[].url` is real (converted-and-served, or verified public)
- [ ] `version` and `initStrategy` set; `layout` has at least one component

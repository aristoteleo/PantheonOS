---
id: viv_live_view
name: View Bioimages with the Viv LiveView
description: |
  Open and drive an interactive Viv image viewer (high-resolution
  multiplexed bioimaging — OME-TIFF, OME-Zarr / OME-NGFF) in the Pantheon
  sidebar via the live_view tools. Covers cloud and local images, the
  channel model used to drive the view, and how to verify it.
tags: [viv, bioimage, microscopy, ome-tiff, ome-zarr, ome-ngff, imaging, multiplexed, live-view]
---

# Viewing Bioimages with Viv

Viv is a WebGL viewer for **high-resolution, multiplexed bioimaging** —
multichannel fluorescence / microscopy images in **OME-TIFF** or
**OME-Zarr (OME-NGFF)**. Open it as a LiveView and drive it.

## Viv vs Vitessce — which to use

- **Viv** — the data is *an image*: OME-TIFF / OME-Zarr, multichannel
  microscopy, IF/IMC/CODEX, whole-slide images. You want channel controls
  (colors, contrast, on/off), pan/zoom, an overview inset.
- **Vitessce** — the data is *spatial omics with cells*: embeddings (UMAP),
  cell sets, gene-expression matrices, spatial scatterplots — possibly with
  an image layer alongside. Use the `vitessce` skill.

If the user just wants to look at a microscopy / OME image, use Viv.

**Cell segmentation / boundary overlay → use Viv.** For "show the cell
segmentation on the image" / "outline the cells", Viv is the right tool —
render the boundaries as an extra channel (see "Overlaying a cell
segmentation" below). It is simpler and the outlines are crisper than
Vitessce. Reach for Vitessce's `obsSegmentations` *only* when the user
genuinely needs **interactive** cells — click/hover a cell, colour cells by
type or gene, link them to a UMAP/heatmap. For a purely visual boundary
overlay, Vitessce is the wrong tool (its segmentation rendering does not
show clean boundaries) — use Viv.

## Quick demo — built in, do not search

For a quick demo, call open_live_view for the viv viewer with **no `state`**:

```
open_live_view(view_type="viv", title="Bioimage Viewer Demo")
```

A verified public OME-TIFF (Vanderbilt 4-channel kidney mxIF) loads
automatically via the bundled `demo.json`. Do NOT web-search for an image.

## The state — what Viv shows

For Viv the LiveView **state** describes the image and its channels:

```jsonc
{
  "url": "https://.../image.ome.tif",   // REQUIRED — see "Cloud vs local"
  "type": "ome-tiff",                    // optional: "ome-tiff" | "ome-zarr"
                                         //   (inferred from the URL if omitted)
  "channels": [                          // optional — omit to auto-fill
    { "selection": {"c": 0, "t": 0, "z": 0},
      "color": [0, 0, 255], "contrastLimits": [0, 5000], "visible": true },
    { "selection": {"c": 1, "t": 0, "z": 0},
      "color": [0, 255, 0], "contrastLimits": [0, 8000], "visible": true }
  ],
  "overview": true                       // optional — overview inset (default on)
}
```

**Omit `channels`** and the adapter picks sensible defaults (one entry per
channel, distinct colors, **auto-contrast**) and reports them back — so
`live_view_get_state` then shows you the exact channel list to drive.

## Input must be OME-TIFF or OME-Zarr

Viv loads **OME-TIFF** (a TIFF carrying embedded OME-XML metadata) or
**OME-Zarr / OME-NGFF**. A plain or ImageJ TIFF (e.g. a `MAX_*.tif`
projection, a raw microscope export) has **no OME metadata** — Viv rejects
it with a Zod error like `path: ["Image", 0] … Required`.

So before opening a `.tif` whose OME status is unknown, **convert it**:

```python
# plain TIFF -> OME-TIFF, with python_interpreter (pip install tifffile)
import tifffile
img = tifffile.imread("input.tif")          # e.g. shape (C, Y, X)
tifffile.imwrite("output.ome.tif", img, metadata={"axes": "CYX"}, ome=True)
```

Then serve and open `output.ome.tif`. (`bioformats2raw` → OME-Zarr is the
heavier-duty alternative for whole-slide / pyramidal data.) Already-OME
inputs and OME-Zarr stores need no conversion.

## Cloud vs local images

- **Cloud** — pass a public `url` directly (OME-TIFF needs HTTP range
  support; OME-Zarr is a directory of chunks). Verify the URL is real.
- **Local** — the user's own file. Serve it, then pass the served URL:
  ```
  serve_local_data("/path/to/image.ome.tif")   -> { url }
  open_live_view("viv", title, state={ "url": <that url> })
  ```
  `serve_local_data` works for both a single OME-TIFF file and an OME-Zarr
  *directory*, and supports range requests — so local OME-TIFF works too.
  Do not invent URLs; only use a verified public URL or a served local path.

## Overlaying a cell segmentation (cell boundaries)

To show cell-segmentation results on an image — the common "outline the
cells" / "overlay the mask" request — render the segmentation's
**boundaries as an extra channel** of the image, then open it in Viv. This
gives a crisp outline overlay with full channel control, and is the
preferred way to *view* a segmentation (simpler and cleaner than Vitessce).

```python
import numpy as np, tifffile
from skimage.segmentation import find_boundaries

img  = tifffile.imread("image.ome.tif")     # (C, Y, X)
mask = np.load("masks.npy")                 # integer label image, 1 per cell
maxv = np.iinfo(img.dtype).max if np.issubdtype(img.dtype, np.integer) else 1
boundary = (find_boundaries(mask, mode="inner") * maxv).astype(img.dtype)
overlay = np.concatenate([np.atleast_3d(img.swapaxes(0, -1)).swapaxes(0, -1)
                          if img.ndim == 3 else img[None],
                          boundary[None]], axis=0)
tifffile.imwrite("overlay.ome.tif", overlay,
                 metadata={"axes": "CYX"}, ome=True)
```

(In practice: just append `boundary` as one more channel so the result is
`(C+1, Y, X)`.) Serve `overlay.ome.tif` and open it in Viv — the last
channel is the cell outlines; colour it bright (white/yellow), the others
dimmer. The user can toggle/recolour it with the channel panel.

This is a *visual* overlay — outlines are pixels, not selectable objects.
Only if the user needs to **click/hover individual cells or colour them by
data** is Vitessce's `obsSegmentations` warranted (see the `vitessce`
skill). For "just show me the cell boundaries", stay in Viv.

## Driving the view

The view has an on-screen **Channels panel** (top-right): the user toggles
visibility, picks colors, and drags contrast sliders there.

The agent drives the same `channels`. `channels` is a JSON **array** — change
it by **replacing the whole array**, not with an index-keyed patch (a
deep-merged dict would corrupt the array):

1. `live_view_get_state(view_id)` → take `state.channels` (the array, incl.
   the auto-filled defaults and any edits the user made in the panel).
2. Edit that array in place — e.g. set a channel's `visible`, `color`,
   `contrastLimits`, or `selection`.
3. `live_view_update(view_id, {"channels": <the full edited array>})`.

Each channel is `{selection:{c,t,z}, color:[r,g,b], contrastLimits:[lo,hi],
visible:bool}`. Always `live_view_get_state` first — never assume the
channel list; it carries the user's panel edits.

## Verify it

Viv renders with WebGL, but it registers a snapshot provider that reads its
own canvas — so **`live_view_screenshot` works for Viv** and returns the
actual rendered image. To verify a view:

1. `live_view_get_state(view_id)` — `status` should be `ready` and
   `diagnostics` empty. A diagnostic here usually means a bad/unreachable
   URL or a non-OME TIFF (see "Input must be OME-TIFF or OME-Zarr").
2. `live_view_screenshot(view_id)` → `observe_images` the result — confirm
   the image actually rendered (channels visible, not all black). If it
   looks black, the contrast limits are off — adjust per-channel
   `contrastLimits` via `live_view_update`.

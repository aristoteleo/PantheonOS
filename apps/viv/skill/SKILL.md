---
id: viv
name: Viv — Bioimages on the desktop
description: |
  Open and drive the Viv desktop app (high-resolution multiplexed
  bioimaging — OME-TIFF, OME-Zarr / OME-NGFF, plain TIFF via automatic
  conversion). Covers opening files and cloud images, the channel model,
  steering any Viv window — including ones the user opened — and how to
  verify what is on screen. This skill is the reference example of the
  Atrium app-skill format.
tags: [viv, bioimage, microscopy, ome-tiff, ome-zarr, imaging, multiplexed, desktop]
---

# Viv — Bioimages

Viv is the desktop's WebGL viewer for **high-resolution, multiplexed
bioimaging**: multichannel fluorescence / microscopy in **OME-TIFF** or
**OME-Zarr (OME-NGFF)**, and plain TIFF too — its backend converts
automatically. Drive it with the `desktop` tools (load the `desktop` skill
if you have not).

## Viv vs Vitessce — which to use

- **Viv** — the data is *an image*: OME-TIFF / OME-Zarr, multichannel
  microscopy, IF/IMC/CODEX, whole-slide images. You want channel controls
  (colors, contrast, on/off), pan/zoom, an overview inset.
- **Vitessce** — the data is *spatial omics with cells*: embeddings,
  cell sets, expression matrices. Use the vitessce app's skill.

**Cell segmentation / boundary overlay → Viv** (render boundaries as an
extra channel; see below). Vitessce's segmentation is only for genuinely
interactive cells (click/hover, colour by data).

## Opening

```python
# A file — plain TIFF included; the backend converts to pyramidal OME
# automatically. This is a double-click as a tool call:
w = desktop_open(path="/workspace/scan.ome.tif")["result"]["window_id"]

# A cloud image — state instead of a file (verify the URL is real;
# OME-TIFF needs HTTP range support):
w = desktop_open(app="viv", state={
    "url": "https://host/image.ome.tif", "type": "ome-tiff",
})["result"]["window_id"]

# Bare — the bundled demo (a verified 4-channel kidney mxIF) loads:
w = desktop_open(app="viv")["result"]["window_id"]
```

Extensions that route to Viv on a plain `desktop_open(path=...)`:
`.ome.tif`, `.ome.tiff`, `.ome.zarr`, `.zarr`, `.tif`, `.tiff`.
Never `serve_local_data` a file just to view it — `desktop_open` runs the
whole pipeline.

## The state — what Viv shows

```jsonc
{
  "url": "https://.../image.ome.tif",   // the served image (set for you on file opens)
  "type": "ome-tiff",                    // "ome-tiff" | "ome-zarr" (inferred if omitted)
  "channels": [                          // omit to auto-fill with sensible defaults
    { "selection": {"c": 0, "t": 0, "z": 0},
      "color": [0, 0, 255], "contrastLimits": [0, 5000], "visible": true }
  ],
  "overview": true                       // overview inset (default on)
}
```

**Omit `channels`** and the adapter picks defaults (one entry per channel,
distinct colors, auto-contrast) and reports them back — `desktop_read`
then shows the exact channel list to drive.

## Steering — any Viv window, including the user's

```python
wins = desktop_windows()["result"]["windows"]
w = next(x["window_id"] for x in wins if x["app_id"] == "viv")

state = desktop_read(w)["result"]["state"]      # ALWAYS read first
chans = state["channels"]                        # carries the user's panel edits
chans[1]["visible"] = False
chans[0]["color"] = [255, 255, 0]
desktop_update(w, {"channels": chans})           # replace the WHOLE array
```

`channels` is a JSON **array** — change it by replacing the whole array,
never an index-keyed patch (a deep-merged dict would corrupt it). Each
entry: `{selection:{c,t,z}, color:[r,g,b], contrastLimits:[lo,hi],
visible:bool}`.

**Fix the window you have.** A wrong view is corrected in place —
`desktop_update` to patch, `desktop_set(window_id, state)` to replace the
whole state, `desktop_open(path=..., window_id=...)` to show a different
image in that window. Opening again is for a genuinely new image;
reopening the same file just focuses the window that already has it.

Actions (`desktop_call(w, ...)`): `showChannels()` opens the channel
panel. `desktop_call(w, "$close")` closes the window.

Backend (`app_call(app_id="viv", ...)`): `prepare(path)` converts any
TIFF/Zarr to a Viv-openable form and returns `{url, type, channels?}` —
`desktop_open(path=...)` calls it for you; call it directly only when you
want the served URL without opening a window. Live signatures:
`app_registry()`.

## Whole-slide / RGB brightfield (H&E `.svs`, `.ndpi`)

A brightfield WSI is **RGB**, not fluorescence: treat it as 3 channels —
R, G, B — that blend back to natural color. Two traps both render BLACK:

- ❌ fluorescence colors / auto-contrast on R/G/B planes. Use **pure R, G,
  B at `contrastLimits: [0, 255]`**.
- ❌ a hand-rolled pyramid with `subfiletype=1` but no `subifds=` reserved
  on the base → `No image at index 1`. For a look, skip the pyramid — one
  downsampled level is plenty.

```python
import openslide, tifffile, numpy as np
slide = openslide.OpenSlide("slide.svs")
lvl = 1 if slide.level_count > 1 else 0
img = np.array(slide.read_region((0, 0), lvl, slide.level_dimensions[lvl]).convert("RGB"))
slide.close()
tifffile.imwrite("slide.ome.tif", np.moveaxis(img, -1, 0),
                 photometric="minisblack", tile=(512, 512), compression="jpeg",
                 metadata={"axes": "CYX"})

w = desktop_open(path="slide.ome.tif")["result"]["window_id"]
desktop_update(w, {"channels": [
    {"selection": {"c": 0, "t": 0, "z": 0}, "color": [255, 0, 0], "contrastLimits": [0, 255], "visible": True},
    {"selection": {"c": 1, "t": 0, "z": 0}, "color": [0, 255, 0], "contrastLimits": [0, 255], "visible": True},
    {"selection": {"c": 2, "t": 0, "z": 0}, "color": [0, 0, 255], "contrastLimits": [0, 255], "visible": True},
]})
```

(True pyramid, when full resolution matters: write levels as sub-IFDs with
`subifds=` reserved on the base, or `bioformats2raw` → `raw2ometiff`.)

## Overlaying a cell segmentation

Render the segmentation's **boundaries as one extra channel**, then open:

```python
import numpy as np, tifffile
from skimage.segmentation import find_boundaries

img  = tifffile.imread("image.ome.tif")     # (C, Y, X)
mask = np.load("masks.npy")                 # integer label image
maxv = np.iinfo(img.dtype).max if np.issubdtype(img.dtype, np.integer) else 1
boundary = (find_boundaries(mask, mode="inner") * maxv).astype(img.dtype)
overlay = np.concatenate([img if img.ndim == 3 else img[None], boundary[None]])
tifffile.imwrite("overlay.ome.tif", overlay, metadata={"axes": "CYX"}, ome=True)

desktop_open(path="overlay.ome.tif")
```

Colour the last channel bright (white/yellow), the rest dimmer. Outlines
are pixels, not clickable objects — that is the point for a visual check.

## Verify it

1. `desktop_read(w)` — the state should carry your url/channels; a missing
   or unchanged state means the window is still loading or the open failed.
2. `desktop_screenshot(w)` — Viv registers its own canvas snapshot, so the
   image shows the ACTUAL render. All black → contrast limits are wrong;
   fix per-channel `contrastLimits` via `desktop_update`.

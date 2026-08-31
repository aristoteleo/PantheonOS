---
id: volume3d
name: 3D Volume (MIP / ISO) — on the desktop
description: |
  Open and drive a 3D volumetric renderer (maximum-intensity projection or
  iso-surface) on the desktop — for a dense 3D scalar image: OPT /
  light-sheet / micro-CT / MRI scans, confocal z-stacks, segmentation
  probability maps. The user's input is usually a 3D TIFF stack (or NIfTI /
  numpy); the recipe converts it to an OME-NGFF Zarr pyramid. Rotate, switch
  MIP/ISO, tune the iso threshold. app="volume3d".
tags: [visualization, volume, 3d, bioimage, mip, iso, tiff, nifti, ome-zarr]
---

# volume3d — 3D volumetric (MIP / ISO) viewer

A GPU volume renderer for a **3D scalar field** stored as an OME-NGFF (OME-Zarr)
multiscale pyramid. Ray-casts the volume as a **maximum-intensity projection
(MIP)** or an **iso-surface (ISO)**. Ported from the Virtual Embryo atlas
(`Volume3DViewer`), built on Three.js' volume shader + zarrita.

Use it for any single-channel 3D intensity volume: OPT / light-sheet / micro-CT
/ MRI scans, confocal z-stacks, segmentation probability maps, a 3D density
field, etc. For **2D** multiplex images use `viv`; for **3D cell point clouds**
(spatial transcriptomics) use `spatial3d`; this viewer is for a dense 3D
*scalar volume*.

## How it renders (so you can reason about the output)

The pipeline auto-frames the specimen with no manual tuning: percentile-clip →
3-pass box smooth → histogram mode → **histology polarity auto-invert** (if the
volume stores tissue dark on a bright background, it is inverted so "bright =
tissue") → Otsu threshold → crop to the foreground bounding box (hides scanner
/ sectioning artefacts) → upload as a `Data3DTexture`. A coarse pyramid level is
auto-picked so the texture stays under **32 M voxels** (GPU + download budget).

## Data prep — write an OME-NGFF **Zarr v2** pyramid

The viewer reads a Zarr **group** whose `.zattrs` declares `multiscales`, with
each level an array at `0/`, `1/`, … in **`[Z, Y, X]`** axis order. Two hard
requirements:

1. **Zarr v2** (the level-probe fetches each level's `.zarray`). With
   `zarr-python` 3.x you MUST pass `zarr_format=2`, or you get a v3 store
   (`zarr.json`, no `.zarray`) the viewer can't level-probe.
2. **3D only** — shape `(Z, Y, X)`. Squeeze any channel/time axis first; this
   viewer renders one scalar field.

```python
import numpy as np, zarr
from zarr.storage import LocalStore        # zarr 3.x  (zarr 2.x: zarr.DirectoryStore)

def write_ome_zarr_v2(vol, out_dir, voxel=(1.0, 1.0, 1.0), n_levels=4):
    """vol: 3D ndarray [Z,Y,X]. voxel: (vz,vy,vx) µm. Writes a v2 pyramid."""
    vol = np.squeeze(np.asarray(vol))                       # drop singleton C/T axes
    if vol.ndim != 3:
        raise ValueError(f"need a 3D [Z,Y,X] volume; got {vol.shape} — keep one channel/timepoint")
    store = LocalStore(str(out_dir))                        # v2: zarr.DirectoryStore(str(out_dir))
    root = zarr.open_group(store=store, mode="w", zarr_format=2)
    datasets, cur = [], vol
    for lvl in range(n_levels):
        z, y, x = cur.shape
        a = root.create_array(str(lvl), shape=cur.shape, dtype=cur.dtype,
                              chunks=(min(64, z), min(64, y), min(64, x)))
        a[...] = cur
        s = [voxel[i] * (2 ** lvl) for i in range(3)]
        datasets.append({"path": str(lvl),
                         "coordinateTransformations": [{"type": "scale", "scale": s}]})
        if min(cur.shape) <= 32:
            break
        cur = cur[::2, ::2, ::2]                             # 3D downsample (ome-zarr Scaler is 2D-only)
    root.attrs["multiscales"] = [{
        "version": "0.4",
        "axes": [{"name": "z", "type": "space", "unit": "micrometer"},
                 {"name": "y", "type": "space", "unit": "micrometer"},
                 {"name": "x", "type": "space", "unit": "micrometer"}],
        "datasets": datasets,
    }]
    return out_dir

# Load YOUR volume — a 3D TIFF stack is the common case:
import tifffile
vol = tifffile.imread("scan.tif")               # multi-page TIFF / OME-TIFF stack → (Z,Y,X)
#   NIfTI:   import nibabel as nib; vol = nib.load("scan.nii.gz").get_fdata()
#   numpy:   vol = np.load("scan.npy")
# If it carries a channel/time axis, keep one 3D field first, e.g. vol = vol[..., 0] or vol[0].
out = write_ome_zarr_v2(vol, "/workspace/volume.ome.zarr", voxel=(8.0, 8.0, 8.0))
```

Then serve the directory and get a browser URL (the data server adds CORS +
Range + `no-store`, which zarrita needs):

```python
url = serve_local_data("/workspace/volume.ome.zarr")   # → http://…/<hash>/volume.ome.zarr
```

Verify before opening: `ls volume.ome.zarr` should show `.zattrs`, `.zgroup`,
and numeric level dirs each containing `.zarray` (v2 markers — **not**
`zarr.json`).

## Open

```python
desktop_open(app="volume3d",
    title="Light-sheet — E12.5",
    state={"url": url, "mode": "iso", "threshold": 0.45, "brightness": 1.2},
)
```

`desktop_open(app="volume3d")` with no state loads a small demo embryo
(needs network + CORS on the public tiles host).

## Drive it

Patch any field; `url`/`level` rebuild, everything else updates live:

```python
desktop_update(window_id, {"mode": "mip"})            # switch MIP ⇄ ISO
desktop_update(window_id, {"threshold": 0.6})          # raise ISO surface level
desktop_update(window_id, {"brightness": 1.8})         # brighten dim tissue
desktop_update(window_id, {"flipUp": True})            # 180° about X (head-up/down)
desktop_update(window_id, {"camera": {                 # restore a saved angle
    "position": [1200, 900, 1200], "target": [0, 0, 0], "zoom": 1.0}})
desktop_screenshot(window_id)                          # reads the WebGL canvas
```

The user can also orbit (drag), tune the control bar, and flip; their camera
angle **round-trips back to you** in the view state (`state.camera`), so a
screenshot you take reflects what they're looking at.

## State reference

| field | type | default | effect |
|---|---|---|---|
| `url` | string | — | OME-NGFF pyramid group URL. Change → rebuild. |
| `level` | int \| `"auto"` | `"auto"` | pyramid level; `auto` picks coarsest ≤32M voxels. Change → rebuild. |
| `mode` | `"mip"` \| `"iso"` | `"iso"` | MIP (brightest along ray) vs ISO (iso-surface). Live. |
| `threshold` | 0..1 | `0.45` | ISO iso-surface level. Live. |
| `brightness` | 0.1..3 | `1.2` | compresses the upper window so dim tissue lights up. Live. |
| `flipUp` | bool | `false` | 180° about world X. Live. |
| `title` | string | `"Volume"` | header label. Live. |
| `camera` | object | — | `{position:[x,y,z], target:[x,y,z], zoom}`; round-trips on orbit. |

## Gotchas

- **Axis order is `[Z, Y, X]`** — the `multiscales` axes must be z,y,x and the
  arrays shaped to match, or the volume renders transposed/sheared.
- **Zarr v2 only** (see data prep). A v3 store has no `.zarray` → the level
  probe fails.
- **One scalar channel.** Squeeze channel/time dims before writing.
- **MIP vs ISO**: MIP is robust for "show me the whole signal"; ISO needs a
  decent `threshold` (start ~0.45 and nudge). For histology-derived volumes
  ISO can degenerate to a saturated block — prefer MIP there.
- **Big volumes**: leave `level:"auto"`. Forcing a fine `level` on a multi-
  hundred-MB level can OOM the GPU 3D texture.
- Needs CORS + HTTP Range; `serve_local_data` / the desktop's data server
  provide both. A bare static host without CORS (e.g. the public demo tiles)
  will be blocked by the browser.

---

## Desktop runtime (Atrium)

This viewer is installed as the desktop app `volume3d` ("Volume 3D"). Everything above drives it through the `desktop` tools, which reach every window of it — including ones the USER opened. What follows is what is specific to this app.

- **Open a file**: `desktop_open(path="/path/to/file")` — `.zarr`, `.ome.zarr` route here through the app's own open pipeline (format conversion, backend prepare) — no serve_local_data needed. Returns `window_id`.
- **Open by state**: `desktop_open(app="volume3d", state={...})` with the state contract documented above.
- **Force this viewer** for a file another app also claims: `desktop_open(app="volume3d", path=...)`. `desktop_apps()` lists every installed app with its id and file claims.
- **Drive any window** (yours or the user's): `desktop_windows()` lists them; `desktop_read(window_id)` returns the current state; `desktop_update(window_id, patch)` deep-merges; `desktop_call(window_id, action, args)` runs the same handlers the app's menus trigger. `desktop_call(w, "$close")` closes.
- **Fix in place**: when a view comes out wrong, correct THAT window (update/set/call, or `desktop_open(path=..., window_id=...)` for a different file) — do not open another window.

### Actions

- `loadExample()` — Show a synthetic volume

### Backend methods

Heavier work runs in this app's own process: `app_call(app_id="volume3d", method=..., args={...})`. List the live method signatures with `app_registry()`.

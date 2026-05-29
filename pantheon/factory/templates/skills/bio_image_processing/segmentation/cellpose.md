---
id: cellpose_segmentation
name: Cell Segmentation with Cellpose
description: |
  Cell and nucleus segmentation using Cellpose v4.x (Cellpose-SAM).
  Covers the v4.x API changes, model selection, GPU/CPU inference,
  image restoration, fine-tuning, 3D segmentation, and batch processing.
tags: [segmentation, cellpose, cellpose-sam, cpsam, nucleus, cell, 3d]
---

# Cell Segmentation with Cellpose

Cellpose is a generalist deep-learning model for cell and nucleus
segmentation. Cellpose v4.x ships a single unified model (`cpsam`,
Cellpose-SAM with a ViT-L backbone) and introduces breaking API changes
from earlier versions.

## 1. Prerequisites

```bash
python -m venv .venv-cellpose
source .venv-cellpose/bin/activate
pip install cellpose
```

> [!WARNING]
> Cellpose uses PyTorch. Install in a **separate virtual environment** from
> TensorFlow-based tools (StarDist, Mesmer). InstanSeg can share this environment.

GPU is optional but strongly recommended. Supports CUDA and Apple MPS.
CPU works but is roughly 5-10x slower (a 1024x1024 image takes ~300s on
CPU vs ~10-30s on GPU).

## 2. Model Selection

In Cellpose v4.x the model landscape has changed significantly:

- The only model shipped is `cpsam` (Cellpose-SAM, ViT-L backbone with
  flow-field prediction head). `MODEL_NAMES` contains only `['cpsam']`.
- Legacy model names (`cyto3`, `nuclei`, `cyto`, `cellpose_sam`) are no
  longer separate models. The package auto-selects `cpsam` regardless of
  what you pass as `model_type`.
- On CPU, `cyto3` and `cpsam` produce identical results (tested on a
  1024x1024 embryo DAPI image: both detect 955 cells in ~310s). Speed
  differences only appear on GPU.

## 3. Basic Segmentation (v4.x API)

```python
from cellpose import models

model = models.CellposeModel(gpu=True)  # or gpu=False for CPU

masks, flows, styles = model.eval(
    img,                    # (H, W) or (H, W, C) or (C, H, W)
    diameter=None,          # auto-estimate; or specify in pixels
    flow_threshold=0.4,
    cellprob_threshold=0,
)
# masks: (H, W) integer array, 0=background, 1..N=cell IDs
```

**Critical v4.x changes from earlier versions:**
- `models.Cellpose` no longer exists. Use `models.CellposeModel`.
- `model.eval()` returns 3 values `(masks, flows, styles)`, not 4.
- The `model_type` argument is silently ignored in v4.0.1+.
- The `channels` parameter is deprecated and silently ignored. Cellpose
  uses the first 3 channels automatically.

## 4. Multi-Channel Images

```python
# For (C, H, W) or (H, W, C) images, Cellpose uses first 3 channels
# No need to specify channels in v4.x
masks, flows, styles = model.eval(img_multichannel, diameter=None)
```

## 5. Image Restoration (Cellpose 3)

Cellpose 3 integrates image restoration (denoising, deblurring, upsampling)
directly into the segmentation pipeline:

```python
from cellpose import denoise

dn_model = denoise.DenoiseModel(model_type='cyto3', gpu=True)
masks, flows, styles = dn_model.eval(
    img,
    diameter=None,
    restore_type='denoise_cyto3',  # or 'deblur_cyto3', 'upsample_cyto3'
)
```

Available `restore_type` values:
- `denoise_cyto3` / `denoise_nuclei` -- remove Poisson/Gaussian noise
- `deblur_cyto3` / `deblur_nuclei` -- correct optical blur
- `upsample_cyto3` / `upsample_nuclei` -- super-resolve low-resolution images

## 6. Batch Processing

```python
from cellpose import io

files = io.get_image_files('/path/to/images/')
for f in files:
    img = io.imread(f)
    masks, flows, styles = model.eval(img, diameter=None)
    io.save_masks(img, masks, flows, f, save_txt=False)
```

## 7. Fine-Tuning

```python
model = models.CellposeModel(gpu=True)
model.train(
    train_data, train_labels,
    n_epochs=100,
    learning_rate=0.1,
    save_path='/path/to/model/',
)
```

Training data format: pairs of images and 16-bit label masks where each
cell has a unique integer ID.

## 8. 3D Segmentation

```python
masks_3d = model.eval(
    volume,          # (Z, Y, X) or (Z, Y, X, C)
    diameter=None,
    do_3D=True,      # true volumetric
    # OR stitch_threshold=0.5 for faster 2D+stitch
)
```

Two approaches:
- `do_3D=True` -- true volumetric segmentation (slower, more accurate for
  isotropic voxels). Very memory-intensive.
- `stitch_threshold=0.5` -- segment each Z-plane in 2D, then stitch masks
  across planes using IoU. Much faster and often sufficient, especially
  when Z-spacing is larger than XY pixel size.

## Common Pitfalls

1. **v4.x API breaking change**: `models.Cellpose` is removed. Use
   `models.CellposeModel`. Old tutorials and code using `models.Cellpose`
   will raise `AttributeError`.

2. **`model_type` and `channels` deprecated**: In v4.0.1+, these arguments
   are silently ignored. Do not rely on them to select a specific model or
   specify channel order.

3. **diameter=None for auto-estimation**: Works well for most cases. If
   cells are very small (<10px) or very large (>200px), measure and specify
   manually.

4. **flow_threshold**: Default 0.4. Increase to 0.8 to reduce
   over-segmentation; decrease to 0.2 for under-segmented images.

5. **CPU vs GPU timing**: On CPU, a 1024x1024 image takes ~300s. On GPU,
   expect ~10-30s. For batch processing, GPU is essential.

6. **3D: do_3D vs stitch**: `do_3D=True` is true volumetric but very slow
   and memory-intensive. `stitch_threshold=0.5` runs 2D per-slice then
   stitches -- much faster and often sufficient.

7. **16-bit images**: Cellpose handles uint16 natively. No need to convert
   to 8-bit.

8. **Image restoration timing**: `DenoiseModel` adds ~50% overhead but
   substantially improves results on noisy or blurred images.

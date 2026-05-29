---
id: mesmer_segmentation
name: Whole-Cell Segmentation with Mesmer (DeepCell)
description: |
  Whole-cell and nuclear segmentation using Mesmer from the DeepCell library.
  Mesmer is a deep learning model trained on tissue imaging data that
  segments cells using nuclear and membrane markers.
tags: [segmentation, mesmer, deepcell, whole-cell, tissue]
---

# Whole-Cell Segmentation with Mesmer (DeepCell)

Mesmer is a deep learning model for whole-cell segmentation from the DeepCell
project. It takes nuclear and membrane/cytoplasm markers as input and produces
instance segmentation masks.

## 1. Prerequisites

```bash
pip install deepcell
```

> [!WARNING]
> DeepCell has strict dependency requirements (specific numpy, TensorFlow versions).
> Installation often fails with version conflicts. Use a **dedicated virtual
> environment** with Python 3.10+ and compatible package versions. Known to fail
> on Python 3.9 with numpy 2.x.

## 2. Basic Nuclear Segmentation

```python
from deepcell.applications import Mesmer
app = Mesmer()

# Input: (batch, H, W, 2) — [nuclear_channel, membrane_channel]
# For nuclear-only, use zeros for membrane channel
import numpy as np
inp = np.stack([nuclear_img, np.zeros_like(nuclear_img)], axis=-1)[np.newaxis, ...]
masks = app.predict(inp, compartment='nuclear')
# masks shape: (1, H, W, 1)
nuclear_labels = masks[0, :, :, 0]
```

## 3. Whole-Cell Segmentation

```python
# Requires BOTH nuclear and membrane/cytoplasm markers
inp = np.stack([nuclear_img, membrane_img], axis=-1)[np.newaxis, ...]
masks = app.predict(inp, compartment='whole-cell')
cell_labels = masks[0, :, :, 0]
```

## 4. Key Parameters

| Parameter | Values | Description |
|---|---|---|
| `compartment` | `'nuclear'`, `'whole-cell'`, `'both'` | What to segment |
| `image_mpp` | float (default ~0.5) | Microns per pixel — affects internal rescaling |
| `batch_size` | int | For multiple images |

## 5. Web Application

DeepCell also offers a web app at https://deepcell.org for drag-and-drop
segmentation without coding.

## Common Pitfalls

1. **Installation is fragile**: DeepCell requires specific numpy + TensorFlow
   version combinations. Create a dedicated environment. `pip install deepcell`
   frequently fails on existing environments.

2. **Two-channel input required**: Even for nuclear-only, you must provide a
   2-channel input `(nuclear, membrane)`. Use zeros for the missing channel.

3. **Input shape**: Must be `(batch, H, W, 2)` — note the channel-last format,
   different from PyTorch tools.

4. **image_mpp matters**: Mesmer was trained at ~0.5 microns/pixel. If your
   images have very different resolution, set `image_mpp` accordingly or the
   internal rescaling will be wrong.

5. **TensorFlow vs PyTorch**: Mesmer uses TensorFlow. Cannot coexist easily
   with PyTorch-based tools (Cellpose, InstanSeg) in the same environment.

6. **Python version**: Requires Python 3.10+. Known to fail on 3.9.

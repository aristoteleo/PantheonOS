---
id: instanseg_segmentation
name: Cell Segmentation with InstanSeg
tags: [segmentation, instanseg, nucleus, cell, multiplexed, fast]
tested_on: embryo DAPI image (1024x1024 uint16)
test_results: 6.7s, nuclei=236, cells=586
test_notes: Very fast, dual output (nuclei + cells simultaneously).
---

# Cell Segmentation with InstanSeg

### 1. Prerequisites
```bash
# Can share a venv with Cellpose (both PyTorch-based)
pip install instanseg-torch
```
No TensorFlow needed. Pure PyTorch. Works on CPU, CUDA, and Apple MPS.
Can be installed in the same environment as Cellpose.

### 2. Available Models
| Model | Description |
|---|---|
| `fluorescence_nuclei_and_cells` | Dual output: nuclei + cells (recommended) |
| `fluorescence_nuclei` | Nuclei only |
| `brightfield_nuclei` | Brightfield images |

Models auto-download on first use (~50MB each).

### 3. Basic Segmentation
```python
import torch
import numpy as np
from instanseg import InstanSeg

model = InstanSeg('fluorescence_nuclei_and_cells', device='cpu')

# Normalize image to [0, 1] float32
img_norm = img.astype(np.float32)
p1, p99 = np.percentile(img_norm, [1, 99])
img_norm = np.clip((img_norm - p1) / (p99 - p1 + 1e-6), 0, 1)

# Input shape: (batch, channels, H, W)
result = model.eval_small_image(torch.tensor(img_norm[None, None]).float())

# result is a TUPLE: (labels_tensor, embeddings_tensor)
# labels_tensor shape: (1, 2, H, W) for nuclei_and_cells model
#   channel 0 = nuclei labels
#   channel 1 = cell labels
nuclei_masks = result[0][0, 0].numpy().astype(int)
cell_masks = result[0][0, 1].numpy().astype(int)
```

> [!WARNING]
> The output is a **tuple**, not a single array. `result[0]` contains labels,
> `result[1]` contains embeddings. Indexing `result[0, 0]` directly will raise
> `TypeError: tuple indices must be integers or slices, not tuple`.

### 4. Multi-Channel / Multiplexed Images
InstanSeg has ChannelNet for arbitrary channel numbers:
```python
# For multi-channel: (batch, C, H, W) with any number of channels
multi_ch = torch.tensor(img_multichannel[None]).float()  # (1, C, H, W)
result = model.eval_small_image(multi_ch)
```

### 5. Large Images (Tiling)
```python
# For images larger than ~2048x2048, use tiled evaluation
result = model.eval_small_image(input_tensor, tile_size=1024, overlap=128)
```

### 6. QuPath Integration
InstanSeg is built into QuPath 0.6.0+. No coding needed — run directly from the QuPath GUI.

### Common Pitfalls
1. **Output is a tuple**: `model.eval_small_image()` returns `(labels, embeddings)`, not just labels. Always index `result[0]` for masks.
2. **Dual output channels**: For `fluorescence_nuclei_and_cells`, `result[0]` has shape `(1, 2, H, W)`. Channel 0 = nuclei, channel 1 = cells. Use the one you need.
3. **Input normalization**: Must be [0, 1] float32. Use percentile-based normalization (1st-99th percentile) for robustness.
4. **No 3D support**: InstanSeg is 2D only. For 3D, use Cellpose or micro-sam.
5. **Speed advantage**: 6.7s vs 310s (Cellpose) on 1024x1024 CPU — ~46x faster. The accuracy trade-off is minor for most use cases.
6. **Cell count vs Cellpose**: InstanSeg detected 586 cells vs Cellpose's 955 on our test image. The difference may be due to different segmentation strategies — check visually which is more appropriate for your data.

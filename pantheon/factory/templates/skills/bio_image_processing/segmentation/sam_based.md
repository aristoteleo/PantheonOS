---
id: sam_cell_segmentation
name: SAM-Based Cell Segmentation
description: |
  Cell segmentation using Segment Anything Model (SAM) adaptations:
  CellSAM, micro-sam, and SAMCell. Covers automatic and interactive
  segmentation modes.
tags: [segmentation, sam, cellsam, micro-sam, foundation-model]
---

# SAM-Based Cell Segmentation

This skill covers cell segmentation tools built on the Segment Anything
Model (SAM), adapted for microscopy. These tools leverage SAM's powerful
vision transformer backbone with microscopy-specific fine-tuning.

> [!WARNING]
> SAM-based tools require **Python 3.10+** and should be installed in a
> **separate virtual environment** from Cellpose/InstanSeg (PyTorch version
> conflicts) and StarDist/Mesmer (TensorFlow conflicts).

## 1. Tool Overview

| Tool | PyPI Package | Python | GPU | Mode |
|---|---|---|---|---|
| CellSAM | Not on PyPI — install from GitHub | 3.10+ | Recommended | Fully automatic |
| micro-sam | `micro-sam` | 3.10+ | Recommended | Auto + interactive |
| SAMCell | `samcell` | 3.9+ | Optional | Automatic (label-free) |

## 2. CellSAM

CellSAM combines a CellFinder detection network (generates bounding-box
prompts automatically) with a fine-tuned SAM decoder for fully automatic
cell segmentation.

### Installation

```bash
# NOT on PyPI — install from source
pip install git+https://github.com/vanvalenlab/cellSAM.git
```

### Basic Usage

```python
from cellSAM import segment_cellular_image

masks = segment_cellular_image(img, device='cuda')
```

> [!TIP]
> CellSAM is also available as a web app at https://cellsam.deepcell.org and
> as a napari plugin, making it accessible without writing any code.

## 3. micro-sam

micro-sam provides both automatic instance segmentation and interactive
annotation through a napari plugin. It supports 2D, 3D, and time-series
data with microscopy-specialized SAM models.

### Installation

```bash
# Requires Python 3.10+
pip install "micro-sam[all]"
```

### Automatic Instance Segmentation

```python
from micro_sam.automatic_segmentation import automatic_instance_segmentation

labels = automatic_instance_segmentation(img, model_type='vit_b_lm')
```

### Interactive (napari plugin)

Launch with `micro_sam.napari` — click points or draw boxes on cells for
instant segmentation. Fine-tune on your data through the napari interface
for domain-specific adaptation.

### Available Models

| Model | Use case |
|---|---|
| `vit_b_lm` | Light microscopy (recommended) |
| `vit_b_em_organelles` | Electron microscopy |
| `vit_t_lm` | Light microscopy (faster, less accurate) |

## 4. SAMCell (Label-Free)

SAMCell is designed for label-free imaging modalities (brightfield, phase
contrast, DIC) where fluorescent markers are not available.

### Installation

```bash
pip install samcell
```

### Basic Usage

```python
from samcell import SAMCellPredictor

predictor = SAMCellPredictor()
masks = predictor.predict(brightfield_img)
```

## Common Pitfalls

1. **Python 3.10+ required**: Both CellSAM and micro-sam require Python 3.10
   or newer. They will NOT install on Python 3.9 (no matching distribution
   found on PyPI).

2. **CellSAM not on PyPI**: Must install from GitHub source. The package name
   `cellsam` does not exist on PyPI.

3. **General SAM is not for cells**: Vanilla SAM/SAM2/SAM3 perform poorly on
   microscopy data (AP ~0.27 vs specialized tools ~0.54). Always use
   microscopy-adapted versions (CellSAM, micro-sam, SAMCell).

4. **GPU strongly recommended**: SAM ViT models are large. CPU inference is
   very slow. ViT-H needs 16GB+ VRAM.

5. **micro-sam multi-channel**: Averages multi-channel inputs to a single
   channel, losing information. For multiplexed data, use InstanSeg's
   ChannelNet instead.

6. **micro-sam model download**: Models auto-download on first use (~400MB
   for vit_b). Ensure internet access.

---
id: cell_segmentation_index
name: Cell Segmentation Skills Index
description: |
  Cell and nucleus segmentation tools for microscopy images.
  Covers Cellpose, SAM-based methods, StarDist, InstanSeg, and Mesmer.
tags: [segmentation, cellpose, sam, stardist, instanseg, mesmer, nucleus, cell]
---

# Cell & Nucleus Segmentation Skills

Instance segmentation tools for cells and nuclei in microscopy images.
Use the tool selection guide below to choose the right method, then load
the corresponding skill file for detailed usage.

## Tool Selection Guide

| Goal | Recommended Tool | Speed | Tested |
|------|-----------------|-------|--------|
| Best overall accuracy | Cellpose-SAM (v4.x) | Moderate (~310s/1024px CPU) | ✅ 955 cells |
| Fastest inference | InstanSeg | **Fast** (~7s/1024px CPU) | ✅ 586 cells |
| Low quality / noisy images | Cellpose 3 (image restoration) | Moderate | ✅ |
| Round nuclei only | StarDist | **Fastest** (~0.5s) | ✅ 150 cells |
| Whole-cell (nucleus + membrane) | Mesmer / DeepCell | Moderate | ⚠️ install issues |
| Interactive annotation / 3D / tracking | micro-sam | Slow | ⚠️ Python 3.10+ |
| Fully automatic, no prompts | CellSAM | Moderate | ⚠️ Python 3.10+ |

> [!TIP]
> Start with **Cellpose** (default in v4.x) for most tasks. It has the best
> generalization. Switch to **InstanSeg** if speed matters or you need
> simultaneous nuclei + cell masks.

> [!WARNING]
> **Environment isolation is important.** These tools have conflicting
> dependencies. Cellpose/InstanSeg use PyTorch; StarDist/Mesmer use TensorFlow;
> SAM-based tools need Python 3.10+. Create **separate virtual environments**
> for each tool family:
> - `venv-cellpose`: Cellpose + InstanSeg (both PyTorch)
> - `venv-stardist`: StarDist (TensorFlow, `numpy<2`)
> - `venv-deepcell`: Mesmer/DeepCell (TensorFlow, strict numpy version)
> - `venv-sam`: micro-sam / CellSAM (Python 3.10+)

## Available Skills

### Cellpose

General-purpose cell and nucleus segmentation using Cellpose v4.x
(includes Cellpose-SAM with ViT-L backbone). Image restoration,
fine-tuning, and 3D segmentation.

**Skill file**: [cellpose.md](./cellpose.md)

**When to use**: Default choice for most segmentation tasks.

### InstanSeg

Fast cell and nucleus segmentation with dual output (nuclei + cells
simultaneously). Supports multiplexed images via ChannelNet.

**Skill file**: [instanseg.md](./instanseg.md)

**When to use**: Speed-critical workflows, multiplexed images, QuPath integration.

### StarDist

Nuclear segmentation using star-convex polygon prediction. Extremely fast
but assumes round/convex nuclei.

**Skill file**: [stardist.md](./stardist.md)

**When to use**: Round nuclei in fluorescence images where speed matters.

### Mesmer / DeepCell

Whole-cell segmentation using both nuclear and membrane markers.
TissueNet-trained PanopticNet architecture.

**Skill file**: [mesmer.md](./mesmer.md)

**When to use**: Tissue images with both nuclear and membrane/cytoplasm markers.

### SAM-Based Cell Segmentation

Cell segmentation using SAM adaptations: CellSAM (automatic), micro-sam
(interactive + 3D), SAMCell (label-free).

**Skill file**: [sam_based.md](./sam_based.md)

**When to use**: Interactive annotation, 3D/tracking, or label-free brightfield.

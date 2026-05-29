---
id: bio_image_processing_index
name: Bio Image Processing Skills Index
description: |
  Skills for biological image analysis: cell/nucleus segmentation,
  image restoration, and spatial data processing.
tags: [image, segmentation, microscopy, cell]
---

# Agent Skills for Biological Image Processing

Best practices and workflows for biological image analysis tasks including
cell segmentation, image restoration, and spatial data processing.
Load the relevant skill files when performing specific analysis tasks.

## Cell & Nucleus Segmentation

Tools and workflows for instance segmentation of cells and nuclei in
microscopy images. Covers deep-learning methods (Cellpose, SAM-based,
StarDist) with guidance on model selection, GPU/CPU inference, fine-tuning,
and 3D segmentation.

**Skill index**: [segmentation/SKILL.md](./segmentation/SKILL.md)

**Skills**:
- **Cellpose**: General-purpose cell/nucleus segmentation (Cellpose 3, Cellpose-SAM)
- **SAM-Based Methods**: CellSAM, micro-sam, SAMCell for automatic and interactive segmentation

**When to use**:
- Segmenting cells or nuclei in fluorescence, brightfield, or phase contrast images
- Need instance masks from 2D or 3D microscopy data
- Comparing or selecting between segmentation tools for your imaging modality
- Fine-tuning a segmentation model on custom training data

---

## Using Skills

1. **Before analysis**: Scan this index for relevant skills
2. **Load skill file**: Read the full skill document for detailed guidance
3. **Follow best practices**: Use the code snippets and workflows provided
4. **Adapt as needed**: Skills are templates; adjust for your specific data

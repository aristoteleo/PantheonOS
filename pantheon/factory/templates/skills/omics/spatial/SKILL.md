---
id: spatial_skills_index
name: Spatial Omics Skills Index
description: |
  Skills for spatial transcriptomics analysis including single-cell to spatial
  mapping (MOSCOT), 3D visualization (PyVista), and related spatial workflows.
tags: [spatial, mapping, 3d, visualization, moscot, pyvista]
---

# Spatial Omics Skills

Skills for spatial transcriptomics data analysis, mapping, and visualization.

## Available Skills

### Single-Cell to Spatial Mapping

Map scRNA-seq to spatial data using optimal transport (MOSCOT) for gene
imputation and cell type transfer.

**Skill file**: [single_cell_spatial_mapping.md](./single_cell_spatial_mapping.md)

**When to use**:
- You have paired scRNA-seq and spatial transcriptomics data
- You want to impute genes not measured in the spatial modality
- You want to transfer cell type annotations to spatial coordinates

### 3D Spatial Data Visualization

Interactive 3D visualization and rotating GIF animations for spatial data
with PyVista.

**Skill file**: [visualize_3d_spatial.md](./visualize_3d_spatial.md)

**When to use**:
- Your spatial data has 3D coordinates
- You want to visualize gene expression or cell types in 3D
- You want to create rotating GIF animations

### Spatial 3D Slice Alignment (Spateo)

Align serial spatial transcriptomics sections into a 3D volume using
Spateo morpho_align with pairwise rigid registration.

**Skill file**: [spatial_3d_alignment.md](./spatial_3d_alignment.md)

**When to use**:
- You have serial tissue sections that need 3D reconstruction
- You want morphology + expression-based slice registration
- You need rigid transformations between consecutive sections

### Spatial Cell-Cell Interaction (Spateo LR)

Infer ligand-receptor interactions between spatially adjacent cell types
using Spateo's two-group CCI analysis with permutation testing.

**Skill file**: [spatial_cci.md](./spatial_cci.md)

**When to use**:
- You want to find LR interactions constrained by spatial proximity
- You have imputed spatial data with mapped cell type labels
- You want to compare spatial vs non-spatial CCI results

### Spatial Deconvolution (Cell2location / Tangram)

Estimate cell type composition at each spatial location using scRNA-seq
reference data. Two-stage model training with Cell2location, or simpler
Tangram alternative.

**Skill file**: [spatial_deconvolution.md](./spatial_deconvolution.md)

**When to use**:
- You want to estimate cell type proportions in spatial data
- You have a scRNA-seq reference with cell type annotations
- You want to impute gene expression via deconvolution

### Spatial Signal Boundary Analysis

Detect expression domain boundaries between spatially antagonistic signals
(e.g., Cer1 restricting Nodal). Includes auto-boundary detection, distance-decay
analysis, and comprehensive 6-panel visualization.

**Skill file**: [spatial_boundary_analysis.md](./spatial_boundary_analysis.md)

**When to use**:
- You have two spatially opposing signals (inhibitor/target)
- You want to quantify spatial restriction of expression domains
- You need publication-quality boundary analysis figures

### Serial H&E Image Registration (RoMa)

Align consecutive H&E histology images using deep dense feature matching
(RoMa + DINOv2) with RANSAC rigid transform estimation and BFS global
composition.

**Skill file**: [he_image_registration.md](./he_image_registration.md)

**When to use**:
- You have serial H&E sections that need global alignment
- You want to build a 3D coordinate frame from histology images
- You need to co-register spatial transcriptomics data with H&E

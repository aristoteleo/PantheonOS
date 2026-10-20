---
id: single_cell_skills_index
name: Single-Cell Analysis Skills Index
description: |
  Core skills for single-cell RNA-seq analysis: quality control, cell type
  annotation, and trajectory inference. These are high-priority actionable
  workflows — load them first for common single-cell tasks.
tags: [single-cell, qc, annotation, trajectory, scanpy]
---

# Core Single-Cell Analysis Skills

High-priority, actionable workflows for the most common single-cell analysis tasks.
For deeper background and alternative methods, see the supplementary
[SC Best Practices](../sc_best_practices/SKILL.md) reference.

## Available Skills

### Quality Control

Standard QC workflow: filtering low-quality cells, doublet detection,
normalization, and QC metric visualization.

**Skill file**: [quality_control.md](./quality_control.md)

**When to use**:
- Starting analysis of a new single-cell dataset
- Need to filter low-quality cells
- Assessing data quality metrics

### Cell Type Annotation

Marker-based and reference-based approaches for assigning cell type labels.

**Skill file**: [cell_type_annotation.md](./cell_type_annotation.md)

**When to use**:
- After clustering, need to assign cell type labels
- Using marker genes for annotation
- Using reference-based methods (CellTypist, scArches)

### Trajectory Inference

Pseudotime analysis and trajectory inference for cell differentiation,
lineage tracing, and RNA velocity.

**Skill file**: [trajectory_inference.md](./trajectory_inference.md)

**When to use**:
- Studying cell differentiation paths
- Neurogenesis or developmental trajectory analysis
- RNA velocity for directional dynamics

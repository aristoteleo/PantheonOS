---
id: omics_skills_index
name: Omics Analysis Skills Index
description: |
  Skills for single-cell and spatial omics data analysis.
  Best practices, code snippets, and workflows for the scverse ecosystem.
---

# Agent Skills for Omics Data Analysis

Here are best practices and workflows for analyzing single-cell and spatial omics data.
When performing specific analysis tasks, load the relevant skill files to guide your approach.

## Available Skills

### Single-Cell to Spatial Mapping

If you have both single-cell and spatial data for the same/similar sample,
you can map single-cell data to spatial data to impute unobserved genes
and enhance spatial resolution.

**Skill file**: [single_cell_spatial_mapping.md](./single_cell_spatial_mapping.md)

**When to use**:
- You have paired scRNA-seq and spatial transcriptomics data
- You want to impute genes not measured in the spatial modality
- You want to transfer cell type annotations to spatial coordinates

---

### 3D Spatial Data Visualization

For visualizing 3D spatial transcriptomics data with interactive plots
and animations.

**Skill file**: [visualize_3d_spatial.md](./visualize_3d_spatial.md)

**When to use**:
- Your spatial data has 3D coordinates
- You want to visualize gene expression or cell types in 3D
- You want to create rotating GIF animations

---

### Quality Control Workflow

Standard quality control workflow for single-cell data.

**Skill file**: [quality_control.md](./quality_control.md)

**When to use**:
- Starting analysis of new single-cell dataset
- Need to filter low-quality cells
- Assessing data quality metrics

---

### Cell Type Annotation

Approaches for annotating cell types in single-cell data.

**Skill file**: [cell_type_annotation.md](./cell_type_annotation.md)

**When to use**:
- After clustering, need to assign cell type labels
- Using marker genes for annotation
- Using reference-based methods

---

### Single-Cell Foundation Models (SCFM)

Workflow and model reference for embedding/integration with foundation models (scGPT, Geneformer, UCE).

**Skill files**:
- [scfm-workflow.md](file:///scfm-workflow.md)
- [scfm-models.md](file:///scfm-models.md)

**When to use**:
- You want FM embeddings (e.g., `obsm["X_uce"]`, `obsm["X_scGPT"]`, `obsm["X_geneformer"]`)
- You need model selection based on gene ID scheme and species
- You want a validation-first workflow before heavy inference

---

### Trajectory Inference

Pseudotime analysis and trajectory inference for cell differentiation,
neurogenesis, and lineage tracing studies.

**Skill file**: [trajectory_inference.md](./trajectory_inference.md)

**When to use**:
- Studying cell differentiation paths (e.g., stem cell → mature cell)
- Neurogenesis analysis (neural progenitors → neurons)
- Comparing developmental trajectories between conditions
- RNA velocity analysis for directional dynamics

---

### Parallel Computing & Performance

Strategies for accelerating single-cell analysis using multi-core CPU,
GPU acceleration, and memory optimization.

**Skill file**: [parallel_computing.md](./parallel_computing.md)

**When to use**:
- Analysis is running slowly on single core
- Dataset has >50k cells and operations are timing out
- GPU is available and you want 10-100x speedup
- Need to parallelize custom analysis loops

---

## Using Skills

1. **Before analysis**: Scan this index for relevant skills
2. **Load skill file**: Read the full skill document for detailed guidance
3. **Follow best practices**: Use the code snippets and workflows provided
4. **Adapt as needed**: Skills are templates; adjust for your specific data

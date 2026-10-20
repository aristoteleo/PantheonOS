---
id: omics_skills_index
name: Omics Analysis Skills Index
description: |
  Skills for single-cell and spatial omics data analysis.
  Best practices, code snippets, and workflows for the scverse ecosystem.
---

# Agent Skills for Omics Data Analysis

Best practices and workflows for single-cell and spatial omics analysis.
Load the relevant skill files when performing specific analysis tasks.

## Core Single-Cell Skills

High-priority, actionable workflows for the most common single-cell analysis tasks.

**Skill index**: [single_cell/SKILL.md](./single_cell/SKILL.md)

**Skills**:
- **Quality Control**: Filtering, doublet detection, normalization, QC metrics
- **Cell Type Annotation**: Marker-based and reference-based label assignment
- **Trajectory Inference**: Pseudotime, lineage tracing, RNA velocity

---

## Gene Panel Selection

End-to-end workflow for designing gene panels in scRNA-seq and spatial
transcriptomics (HVG/DE/RF/scGeneFit/SpaPROS), with sub-panel discovery,
consensus scoring, biological completion, and benchmarking.

**Skill folder**: [gene_panel_selection/](./gene_panel_selection/)

**When to use**:
- Designing a gene panel for spatial transcriptomics in a **static / terminal** system
- Benchmarking existing panels (ARI/NMI/Silhouette + UMAP)
- **IMPORTANT**: When doing gene panel selection, **strictly** follow this workflow

---

## Developmental Gene Panel Design

Panel design for **developing / dynamic** systems (embryonic organs, differentiation, regeneration),
where terminal cell types are end-products of earlier lineage programs. Uses TWO references — the
target (usually late) stage for cell-state resolution, and an independent EARLIER-stage reference
mined by trajectory/pseudotime ranking for developmental regulators — under an explicit budget split.

**Skill folder**: [developmental_gene_panel/](./developmental_gene_panel/)

**When to use** (use this INSTEAD of `gene_panel_selection`):
- The tissue is embryonic / differentiating / regenerating, or the user cares about **lineage origin
  and regulators**, not only terminal cell-type classification
- The assay is at a late stage but the biology is a trajectory
- **Why it matters**: developmental regulators are frequently NOT recoverable from the target stage —
  in a benchmarked case study six unbiased methods (cell-type DE, DPT pseudotime Pearson/Spearman,
  cross-stage ANOVA, global marker rank) all ranked a known CHD regulator in the top 0.6–1.8% on an
  early reference and failed (top 3–79%) on the target stage; literature lookup also failed

---

## Spatial Omics

Skills for spatial transcriptomics mapping, imputation, and 3D visualization.

**Skill index**: [spatial/SKILL.md](./spatial/SKILL.md)

**Skills**:
- **Single-Cell to Spatial Mapping**: Map scRNA-seq to spatial data with MOSCOT
  for gene imputation and cell type transfer
- **3D Spatial Visualization**: Interactive 3D plots and rotating animations
  with PyVista

**When to use**:
- You have paired scRNA-seq and spatial transcriptomics data
- You want to impute genes or transfer cell type labels to spatial coordinates
- Your spatial data has 3D coordinates and you want to visualize them

---

## Single-Cell Foundation Models (SCFM)

Workflow and model reference for embedding/integration with foundation models
(scGPT, Geneformer, UCE, scBERT, etc.).

**Skill index**: [scfm/SKILL.md](./scfm/SKILL.md)

**When to use**:
- You want FM embeddings (e.g., `obsm["X_uce"]`, `obsm["X_scGPT"]`)
- You need model selection based on gene ID scheme and species
- You want a validation-first workflow before heavy inference

---

## Database Access

Tools for querying genomic databases, downloading sequencing data, and
accessing large-scale single-cell datasets programmatically.

**Skill index**: [database_access/SKILL.md](./database_access/SKILL.md)

**Tools covered**:
- **gget**: 23 modules for querying Ensembl, NCBI, UniProt, COSMIC, OpenTargets, etc.
- **iSeq**: CLI for downloading from GSA, SRA, ENA, DDBJ, GEO
- **CZ CELLxGENE Census**: API for 217M+ single-cell observations

---

## Upstream Processing

Technology-specific pipelines for processing raw sequencing data into
analysis-ready count matrices.

**Skill index**: [upstream_processing/SKILL.md](./upstream_processing/SKILL.md)

**Technologies covered**:
- **nf-core Pipelines**: 143+ Nextflow pipelines for scRNA-seq, spatial, bulk,
  ATAC-seq, ChIP-seq, variant calling
- **OpenST**: Open-source spatial transcriptomics processing pipeline

---

## General Data Analysis

Cross-cutting skills for environment setup and computational performance.

**Skill index**: [general_data_analysis/SKILL.md](./general_data_analysis/SKILL.md)

**Skills**:
- **Environment Management**: Conda/Mamba/venv setup for reproducible environments
- **Parallel Computing**: Multi-core CPU, GPU acceleration, memory optimization

---

## Supplementary Reference: SC Best Practices

Comprehensive guidance derived from the
[Single-cell Best Practices](https://www.sc-best-practices.org) book.
Use as supplementary context when the core skills above need deeper background.

**Skill index**: [sc_best_practices/SKILL.md](./sc_best_practices/SKILL.md)

**Topics covered**:
- Preprocessing, normalization, dimensionality reduction
- Clustering, annotation, dataset integration
- Trajectory analysis, RNA velocity, lineage tracing
- Differential expression, compositional analysis, pathway analysis
- Gene regulatory networks, cell-cell communication
- Bulk deconvolution, scATAC-seq, spatial omics
- CITE-seq, immune repertoire (TCR/BCR)
- Multimodal integration, reproducibility

---

## Using Skills

1. **Before analysis**: Scan this index for relevant skills
2. **Load skill file**: Read the full skill document for detailed guidance
3. **Follow best practices**: Use the code snippets and workflows provided
4. **Adapt as needed**: Skills are templates; adjust for your specific data

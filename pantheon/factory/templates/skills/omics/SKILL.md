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

### Gene Panel Selection Workflow

End-to-end workflow for designing **biologically meaningful** and **algorithmically robust**
gene panels in scRNA-seq and spatial transcriptomics (HVG/DE/RF/scGeneFit/SpaPROS),
with sub-panel discovery (ARI vs size), consensus scoring, biological completion
(Completion Rule), and benchmarking on test splits.

**Skill file**: [gene_panel_selection.md](./gene_panel_selection.md)

**When to use**:
- You need to design a gene panel 
- You need to benchmark (ARI/NMI/Silhouette + UMAP similarity) already existing panels

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

## Upstream Processing

Technology-specific pipelines for processing raw sequencing data into
analysis-ready count matrices with spatial coordinates. These cover the steps
that precede standard single-cell analysis (QC, normalization, clustering, etc.).

**Skill index**: [upstream_processing/SKILL.md](./upstream_processing/SKILL.md)

**Technologies covered**:
- **nf-core Pipelines**: 143+ curated Nextflow pipelines for scRNA-seq, spatial
  transcriptomics, bulk RNA-seq, ATAC-seq, ChIP-seq, CUT&Run, methylation,
  and variant calling (WGS/WES)
- **OpenST**: Open-source spatial transcriptomics at sub-cellular resolution —
  flow cell barcode preprocessing, spacemake alignment, image registration,
  Cellpose segmentation, 3D reconstruction, and downstream analysis

**When to use**:
- Processing raw BCL/FASTQ files with nf-core community pipelines
- Running technology-specific alignment and preprocessing pipelines
- Spatial coordinate registration and cell segmentation
- Variant calling from WGS/WES/targeted sequencing
- 3D reconstruction from serial tissue sections

---

## Database Access

Tools for querying genomic databases, downloading sequencing data from public
repositories, and accessing large-scale single-cell datasets programmatically.

**Skill index**: [database_access/SKILL.md](./database_access/SKILL.md)

**Tools covered**:
- **gget**: Python package with 23 modules for querying Ensembl, NCBI, UniProt,
  ARCHS4, Enrichr, COSMIC, OpenTargets, CellxGene, cBioPortal, PDB, and Bgee
- **iSeq**: Bash CLI for downloading sequencing data from GSA, SRA, ENA, DDBJ,
  and GEO databases with parallel downloads and Aspera support
- **CZ CELLxGENE Census**: Cloud-based Python API for accessing 217M+
  single-cell RNA-seq observations with flexible metadata queries and
  pre-computed embeddings

**When to use**:
- Querying gene/protein information from public databases
- Downloading raw sequencing data (FASTQ/SRA) from public repositories
- Accessing curated single-cell RNA-seq datasets by tissue, cell type, disease
- Performing enrichment analysis or cancer mutation queries
- Fetching reference genomes, annotations, and sequences

---

## Supplementary Reference: SC Best Practices

For more comprehensive guidance on single-cell and spatial omics analysis, refer to
the **SC Best Practices** skill collection, derived from the authoritative
[Single-cell Best Practices](https://www.sc-best-practices.org) book. It covers
the full analysis pipeline from preprocessing to reproducibility, including
detailed workflows, method comparisons, and code examples for the scverse ecosystem.

**Skill index**: [sc_best_practices/SKILL.md](./sc_best_practices/SKILL.md)

**Topics covered**:
- Introduction & raw data processing frameworks
- Preprocessing (QC, normalization, HVG, dimensionality reduction)
- Clustering, annotation & dataset integration
- Trajectory analysis (pseudotime, RNA velocity, lineage tracing)
- Differential expression & condition analysis
- Gene regulatory networks & cell-cell communication
- Bulk deconvolution, chromatin accessibility (scATAC-seq)
- Spatial omics (neighborhood analysis, deconvolution, imputation)
- Surface protein (CITE-seq), immune repertoire (TCR/BCR)
- Multimodal integration & reproducibility

When the skills above provide task-specific workflows, these supplementary
references offer broader context, alternative methods, and detailed best practices
to complement your analysis.

---

## Using Skills

1. **Before analysis**: Scan this index for relevant skills
2. **Load skill file**: Read the full skill document for detailed guidance
3. **Follow best practices**: Use the code snippets and workflows provided
4. **Adapt as needed**: Skills are templates; adjust for your specific data

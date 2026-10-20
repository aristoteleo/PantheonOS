---
id: sc_best_practices_index
name: "SC Best Practices Skills Index"
description: |
  Skills derived from the Single-cell Best Practices book (sc-best-practices.org).
  Comprehensive workflows and guidelines for single-cell and spatial omics analysis.
---

# SC Best Practices Skills

Best practices and workflows for single-cell and spatial omics data analysis,
based on the [Single-cell Best Practices](https://www.sc-best-practices.org) book.

When performing specific analysis tasks, load the relevant skill files to guide your approach.

## Available Skills

### Introduction & Fundamentals

Overview of single-cell RNA-seq technologies, raw data processing pipelines,
analysis frameworks, and data format interoperability.

**Skill file**: [introduction.md](./introduction.md)

**When to use**:
- Starting a new single-cell project and choosing technology/tools
- Need guidance on raw data processing (CellRanger, STARsolo, Kallisto)
- Converting between AnnData, SingleCellExperiment, and Seurat formats

---

### Preprocessing & Quality Control

Quality control, ambient RNA removal, doublet detection, normalization,
feature selection, and dimensionality reduction.

**Skill file**: [preprocessing.md](./preprocessing.md)

**When to use**:
- Starting analysis of a new single-cell dataset
- Filtering low-quality cells with MAD-based thresholds
- Choosing normalization and feature selection methods
- Running PCA, UMAP, or t-SNE

---

### Clustering & Cell Type Annotation

Graph-based clustering, resolution selection, manual and automated cell type
annotation, and dataset integration.

**Skill file**: [clustering_and_annotation.md](./clustering_and_annotation.md)

**When to use**:
- Clustering cells with Leiden algorithm
- Annotating cell types using markers or automated tools (CellTypist, scArches)
- Integrating multiple datasets (scVI, scANVI, BBKNN, Harmony)

---

### Trajectory Analysis

Pseudotime inference, RNA velocity, fate prediction, and lineage tracing.

**Skill file**: [trajectory_analysis.md](./trajectory_analysis.md)

**When to use**:
- Studying cell differentiation paths
- Running RNA velocity analysis (scVelo)
- Predicting cell fate with CellRank
- Analyzing lineage tracing data (Cassiopeia)

---

### Differential Expression & Condition Analysis

Differential expression (pseudobulk methods), compositional analysis,
gene set enrichment, and perturbation modeling.

**Skill file**: [differential_and_condition.md](./differential_and_condition.md)

**When to use**:
- Comparing gene expression between conditions
- Running pseudobulk DE analysis with edgeR/DESeq2
- Performing GSEA/pathway analysis with decoupler
- Analyzing compositional changes with scCODA

---

### Gene Regulatory Networks & Cell-Cell Communication

GRN inference with pySCENIC and cell-cell communication analysis
with LIANA, NicheNet, and CellChat.

**Skill file**: [regulatory_and_communication.md](./regulatory_and_communication.md)

**When to use**:
- Inferring gene regulatory networks from scRNA-seq
- Analyzing ligand-receptor interactions between cell types
- Running pySCENIC (GRNBoost2 + motif pruning + AUCell)

---

### Bulk Deconvolution

Estimating cell-type proportions in bulk RNA-seq using single-cell references.

**Skill file**: [bulk_deconvolution.md](./bulk_deconvolution.md)

**When to use**:
- Deconvolving bulk RNA-seq with single-cell reference
- Comparing methods (CIBERSORTx, MuSiC, DWLS, Scaden)
- Validating deconvolution with pseudobulk benchmarks

---

### Chromatin Accessibility (scATAC-seq)

scATAC-seq preprocessing, QC, peak calling, motif analysis, and
GRN inference from chromatin data.

**Skill file**: [chromatin_accessibility.md](./chromatin_accessibility.md)

**When to use**:
- Processing scATAC-seq data (SnapATAC2, ArchR, Signac)
- Assessing QC metrics (TSS enrichment, fragment size distribution)
- Running TF motif enrichment with chromVAR
- Integrating scATAC with scRNA-seq

---

### Spatial Omics

Spatial transcriptomics analysis including neighborhood analysis,
spatial domains, spatially variable genes, deconvolution, and gene imputation.

**Skill file**: [spatial_omics.md](./spatial_omics.md)

**When to use**:
- Analyzing Visium, MERFISH, Xenium, or other spatial data
- Running spatial neighborhood analysis with Squidpy
- Identifying spatial domains (SpaGCN, STAGATE)
- Deconvolving spatial spots (Cell2location)
- Imputing unmeasured genes (Tangram)

---

### Surface Protein (CITE-seq)

CITE-seq / ADT data processing, normalization, quality control,
and joint RNA-protein analysis.

**Skill file**: [surface_protein.md](./surface_protein.md)

**When to use**:
- Processing CITE-seq / ADT data
- Normalizing protein data (CLR, DSB)
- Joint RNA-protein analysis (totalVI, WNN)
- ADT-based cell type annotation

---

### Immune Repertoire (TCR/BCR)

TCR and BCR profiling, clonotype analysis, clonal expansion,
repertoire diversity, and integration with gene expression.

**Skill file**: [immune_repertoire.md](./immune_repertoire.md)

**When to use**:
- Analyzing single-cell TCR/BCR sequencing data
- Clonotype definition and expansion analysis with scirpy
- Measuring repertoire diversity
- Integrating immune receptor data with transcriptomics

---

### Multimodal Integration

Strategies for integrating multi-modal single-cell data including
paired (MOFA+, WNN, MultiVI) and unpaired (GLUE, bridge) approaches.

**Skill file**: [multimodal_integration.md](./multimodal_integration.md)

**When to use**:
- Integrating RNA + ATAC (10x Multiome)
- Integrating RNA + Protein (CITE-seq)
- Working with unpaired multi-modal data
- Choosing between integration strategies

---

### Reproducibility

Environment management, containerization, workflow orchestration,
version control, and documentation standards.

**Skill file**: [reproducibility.md](./reproducibility.md)

**When to use**:
- Setting up a reproducible analysis environment
- Creating Docker/Singularity containers
- Building Snakemake or Nextflow pipelines
- Managing random seeds for deterministic results

---

## Using Skills

1. **Before analysis**: Scan this index for relevant skills
2. **Load skill file**: Read the full skill document for detailed guidance
3. **Follow best practices**: Use the code snippets and workflows provided
4. **Adapt as needed**: Skills are templates; adjust for your specific data

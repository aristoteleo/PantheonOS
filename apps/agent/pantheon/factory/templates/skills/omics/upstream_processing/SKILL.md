---
id: upstream_processing_index
name: Upstream Processing Skills Index
description: |
  Skills for upstream data processing in single-cell and spatial omics,
  covering raw data generation, barcode processing, alignment, spatial
  registration, and technology-specific preprocessing pipelines.
---

# Upstream Processing Skills

Skills and workflows for upstream data processing steps that precede
standard single-cell analysis (QC, normalization, clustering, etc.).
These cover technology-specific pipelines from raw sequencing data
to analysis-ready count matrices with spatial coordinates.

## Available Skills

### OpenST

Open-ST is an open-source spatial transcriptomics technology that captures
transcriptome-wide data at sub-cellular resolution. The computational pipeline
covers flow cell barcode preprocessing, transcriptomic alignment via spacemake,
image-to-coordinate registration, cell segmentation, and 3D reconstruction.

**Skill directory**: [openst/](./openst/)

**When to use**:
- Processing raw Open-ST data from BCL files to spatially-resolved h5ad
- Aligning transcriptomic coordinates to H&E tissue images
- Cell segmentation and transcript-to-cell assignment
- 3D reconstruction from serial tissue sections

---

### nf-core Pipelines

nf-core is a community-driven collection of 143+ curated Nextflow pipelines for
bioinformatics. Skills cover installation, configuration, and pipeline-specific
guides for transcriptomics, spatial omics, epigenomics, and variant calling.

**Skill directory**: [nfcore/](./nfcore/)

**When to use**:
- Processing scRNA-seq data (10x, Drop-seq, Smart-seq) with nf-core/scrnaseq
- Processing spatial transcriptomics (Visium, Xenium, MERSCOPE) with nf-core pipelines
- Processing bulk RNA-seq, ATAC-seq, ChIP-seq, CUT&Run, or methylation data
- Variant calling from WGS/WES with nf-core/sarek
- Setting up Nextflow and nf-core on HPC clusters or cloud environments

---

## Using Skills

1. **Identify your technology**: Find the relevant sub-directory for your spatial/sequencing platform
2. **Load skill files**: Read the full skill documents for step-by-step guidance
3. **Follow the pipeline**: Upstream processing is sequential; follow stages in order
4. **Proceed to downstream analysis**: After generating the count matrix, use the main omics skills for QC, clustering, etc.

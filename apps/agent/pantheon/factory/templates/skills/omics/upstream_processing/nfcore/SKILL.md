---
id: nfcore_skills_index
name: nf-core Pipelines Skills Index
description: |
  Skills for using nf-core community pipelines to process omics data,
  from installation and configuration to running specific analysis pipelines.
---

# nf-core Pipelines Skills

[nf-core](https://nf-co.re/) is a community-driven collection of **143+ curated
Nextflow pipelines** for bioinformatics. All pipelines are open-source (MIT),
rigorously tested, and run portably on laptops, HPCs, and cloud platforms with
automated dependency management via Docker, Singularity, or Conda.

## Available Skills

### Getting Started & Usage

Installation, configuration, and common usage patterns for running any
nf-core pipeline on local machines, HPC clusters, or cloud environments.

**Skill file**: [nfcore_usage.md](./nfcore_usage.md)

**When to use**:
- First time setting up Nextflow and nf-core
- Configuring pipelines for your HPC cluster or cloud environment
- Understanding resource management, resume, and offline execution
- Looking up nf-core CLI tool commands

---

### Single-Cell & Bulk RNA-seq Pipelines

Pipelines for processing single-cell RNA-seq (10x, Drop-seq, Smart-seq)
and bulk RNA-seq data from raw FASTQs to count matrices.

**Skill file**: [nfcore_transcriptomics.md](./nfcore_transcriptomics.md)

**When to use**:
- Processing 10x Chromium, Drop-seq, or Smart-seq scRNA-seq data
- Running downstream single-cell analysis (doublet removal, integration, annotation)
- Processing bulk RNA-seq with STAR, HISAT2, Salmon, or Kallisto
- Generating gene/transcript count matrices and QC reports

---

### Spatial Omics Pipelines

Pipelines for spatial transcriptomics platforms including Visium, Xenium,
MERSCOPE, CosMX, and molecular cartography.

**Skill file**: [nfcore_spatial.md](./nfcore_spatial.md)

**When to use**:
- Processing 10x Visium or Visium HD data
- Analyzing Xenium in situ data with cell segmentation
- Running technology-agnostic spatial pipelines (sopa)
- Processing Resolve Bioscience Molecular Cartography data

---

### Epigenomics Pipelines

Pipelines for chromatin accessibility, histone modification, protein-DNA
interaction, and DNA methylation profiling.

**Skill file**: [nfcore_epigenomics.md](./nfcore_epigenomics.md)

**When to use**:
- Processing ATAC-seq data (bulk)
- Analyzing ChIP-seq experiments with peak calling
- Running CUT&Run or CUT&Tag with spike-in normalization
- Processing bisulfite sequencing or TAPS methylation data

---

### Variant Calling Pipeline (Sarek)

Germline and somatic variant detection from WGS, WES, or targeted
sequencing data with 16+ variant callers.

**Skill file**: [nfcore_variant_calling.md](./nfcore_variant_calling.md)

**When to use**:
- Detecting germline or somatic SNVs, indels, SVs, and CNVs
- Processing tumor/normal pairs or tumor-only samples
- Running multi-caller consensus variant analysis
- Annotating variants with SnpEff or VEP

---

### Hi-C Chromatin Conformation Pipeline

Pipeline for processing Hi-C chromosome conformation capture data to study
3D genome organization: contact maps, TADs, and A/B compartments.

**Skill file**: [nfcore_hic.md](./nfcore_hic.md)

**When to use**:
- Processing Hi-C data (digestion or DNase protocol)
- Generating multi-resolution contact maps (.cool/.mcool)
- Calling TADs and A/B compartments
- Studying 3D genome organization and chromatin interactions

---

### Dynamic Pipeline Discovery (All 143+ Pipelines)

Meta-skill for dynamically discovering and using **any** nf-core pipeline,
including those not covered by the skill files above. Teaches the agent
how to fetch pipeline documentation, parameters, and samplesheet formats
on-the-fly from standardized nf-core URLs and schemas.

**Skill file**: [nfcore_dynamic_discovery.md](./nfcore_dynamic_discovery.md)

**When to use**:
- User asks about a pipeline not covered in the detailed skill files above
- Exploring what pipelines are available for a specific data type
- Need to look up parameters or samplesheet format for any nf-core pipeline
- Pipeline has been updated and you need the latest information

> [!TIP]
> The detailed skill files above cover the most commonly used pipelines with
> full parameter tables and examples. For all other pipelines, use the dynamic
> discovery skill to fetch information on-the-fly from nf-co.re.

---

## Using Skills

1. **Start with usage guide**: Read `nfcore_usage.md` for installation and configuration
2. **Select pipeline skill**: Choose the skill matching your data type
3. **Pipeline not listed?** Use `nfcore_dynamic_discovery.md` to fetch docs on-the-fly
4. **Follow samplesheet format**: Each pipeline requires a specific CSV samplesheet
5. **Test first**: Always run with `-profile test,docker` before real data
6. **Use `-resume`**: Re-run failed pipelines without recomputing successful steps

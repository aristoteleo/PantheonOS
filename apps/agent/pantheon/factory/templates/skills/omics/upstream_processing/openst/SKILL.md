---
id: openst_skills_index
name: OpenST Skills Index
description: |
  Skills for Open-ST spatial transcriptomics data processing,
  from raw BCL files to spatially-resolved single-cell h5ad objects.
---

# OpenST Skills

[Open-ST](https://rajewsky-lab.github.io/openst/) is an open-source spatial
transcriptomics method that captures transcriptome-wide expression at
sub-cellular resolution using sequencing-based spatial barcoding on
Illumina flow cells.

## Available Skills

### Computational Analysis Pipeline

Complete end-to-end computational workflow for processing Open-ST data,
covering all 6 stages from raw data to analysis-ready objects.

**Skill file**: [openst_computational.md](./openst_computational.md)

**When to use**:
- Processing raw Open-ST BCL/FASTQ files
- Running spacemake for transcriptomic alignment
- Aligning spatial coordinates to tissue images
- Segmenting cells and assigning transcripts
- Reconstructing 3D spatial data from serial sections
- Performing downstream exploratory analysis on Open-ST data

---

## Using Skills

1. **Read the computational pipeline skill** for the full step-by-step workflow
2. **Follow stages sequentially**: Each stage depends on the previous one
3. **Check system requirements**: 128 GB RAM recommended, GPU for segmentation/alignment

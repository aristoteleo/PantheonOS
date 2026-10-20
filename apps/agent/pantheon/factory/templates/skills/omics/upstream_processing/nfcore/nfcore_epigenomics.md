---
id: nfcore_epigenomics
name: "nf-core: Epigenomics Pipelines"
description: |
  nf-core pipelines for ATAC-seq, ChIP-seq, CUT&Run/CUT&Tag, and bisulfite/
  TAPS methylation sequencing data processing.
tags:
  - nf-core
  - ATAC-seq
  - ChIP-seq
  - CUT&Run
  - CUT&Tag
  - methylation
  - bisulfite
  - epigenomics
---

# nf-core: Epigenomics Pipelines

## Pipeline Overview

| Pipeline | Assay | Aligners | Peak/Caller | Key Output |
|----------|-------|----------|-------------|------------|
| **atacseq** | ATAC-seq | BWA, Bowtie2, Chromap, STAR | MACS2 | Peaks, bigWig, diff. accessibility |
| **chipseq** | ChIP-seq | BWA, Bowtie2, Chromap, STAR | MACS3 | Peaks, diff. binding, bigWig |
| **cutandrun** | CUT&Run/Tag/TIPseq | Bowtie2 | SEACR, MACS2 | Peaks, heatmaps, spike-in tracks |
| **methylseq** | Bisulfite/TAPS | Bismark, bwa-meth, bwa-mem | N/A | Methylation calls, QC |

---

## nf-core/atacseq

**Latest version**: 2.1.2 | **DOI**: 10.5281/zenodo.2634132

Pipeline for ATAC-seq data: raw reads → alignment → filtering → peak calling →
differential accessibility.

### Samplesheet Format

```csv
sample,fastq_1,fastq_2,replicate
WT,/data/wt_rep1_R1.fastq.gz,/data/wt_rep1_R2.fastq.gz,1
WT,/data/wt_rep2_R1.fastq.gz,/data/wt_rep2_R2.fastq.gz,2
KO,/data/ko_rep1_R1.fastq.gz,/data/ko_rep1_R2.fastq.gz,1
```

| Column | Required | Description |
|--------|----------|-------------|
| `sample` | Yes | Sample name |
| `fastq_1` | Yes | Gzipped R1 FASTQ |
| `fastq_2` | Yes | Gzipped R2 FASTQ |
| `replicate` | Yes | Integer replicate number (starting from 1) |
| `control` | No | Control sample name |

- **Biological replicates**: Same sample name, different replicate numbers
- **Technical replicates**: Same sample name + same replicate number

### Workflow Steps

1. FastQC → Trim Galore! → Alignment (BWA/Bowtie2/Chromap/STAR)
2. Duplicate marking and multi-library merging
3. Extensive filtering: mitochondrial reads, blacklisted regions, duplicates,
   multi-mapping, >4 mismatches, soft-clipped, insert size >2kb
4. Normalized bigWig generation (scaled to 1M mapped reads)
5. Peak calling (MACS2 — broad and narrow modes)
6. Peak annotation (HOMER)
7. Consensus peakset across samples (featureCounts)
8. Differential accessibility (DESeq2 PCA/clustering)
9. ataqv ATAC-seq-specific QC, IGV session files, MultiQC

### Key Parameters

```bash
nextflow run nf-core/atacseq \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --read_length 150 \
  -profile docker
```

| Parameter | Description |
|-----------|-------------|
| `--genome` | Reference genome (e.g., `GRCh38`) |
| `--read_length` | Read length: 50, 100, 150, or 200 bp |
| `--with_control` | Enable control-based peak calling |
| `--save_reference` | Save generated indices |

### Output Files

- MACS2 peak files (narrowPeak/broadPeak), HOMER annotations
- Normalized bigWig coverage tracks
- Consensus peak count matrix, DESeq2 differential accessibility
- ataqv QC reports, IGV session files, MultiQC

---

## nf-core/chipseq

**Latest version**: 2.1.0 | **DOI**: 10.5281/zenodo.3240506

Pipeline for ChIP-seq data: peak calling, QC, and differential binding analysis.

### Samplesheet Format

```csv
sample,fastq_1,fastq_2,replicate,antibody,control,control_replicate
WT_H3K4me3,/data/wt_r1_R1.fastq.gz,/data/wt_r1_R2.fastq.gz,1,H3K4me3,WT_INPUT,1
WT_H3K4me3,/data/wt_r2_R1.fastq.gz,/data/wt_r2_R2.fastq.gz,2,H3K4me3,WT_INPUT,2
WT_INPUT,/data/input_r1_R1.fastq.gz,/data/input_r1_R2.fastq.gz,1,,,
```

| Column | Description |
|--------|-------------|
| `sample` | Sample identifier |
| `fastq_1`, `fastq_2` | FASTQ files (R2 optional for single-end) |
| `replicate` | Replicate number |
| `antibody` | Antibody or histone mark |
| `control` | Control/input sample name |
| `control_replicate` | Control replicate number |

### Workflow Steps

1. FastQC → Trim Galore! → Alignment (BWA/Bowtie2/Chromap/STAR)
2. Duplicate marking (Picard) → Merging → Extensive filtering
3. Alignment QC (Picard, phantompeakqualtools strand cross-correlation)
4. IP enrichment analysis (deepTools)
5. Normalized bigWig generation
6. Peak calling (MACS3 — broad/narrow)
7. Peak annotation (HOMER)
8. Consensus peaks → featureCounts → DESeq2 PCA/clustering
9. IGV session files, MultiQC

### Key Parameters

```bash
nextflow run nf-core/chipseq \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  -profile docker
```

### Output Files

- MACS3 peak files, HOMER annotations
- BigWig tracks, strand cross-correlation plots
- Consensus peak count matrix, DESeq2 differential binding
- IGV session files, MultiQC

---

## nf-core/cutandrun

**Latest version**: 3.2.2 | **DOI**: 10.5281/zenodo.5653535

Pipeline for **CUT&RUN**, **CUT&Tag**, and **TIPseq** protocols with
spike-in normalization support.

### Samplesheet Format

```csv
group,replicate,fastq_1,fastq_2,control
h3k27me3,1,/data/h3k27me3_r1_R1.fastq.gz,/data/h3k27me3_r1_R2.fastq.gz,igg_ctrl
h3k27me3,2,/data/h3k27me3_r2_R1.fastq.gz,/data/h3k27me3_r2_R2.fastq.gz,igg_ctrl
igg_ctrl,1,/data/igg_r1_R1.fastq.gz,/data/igg_r1_R2.fastq.gz,
igg_ctrl,2,/data/igg_r2_R1.fastq.gz,/data/igg_r2_R2.fastq.gz,
```

| Column | Description |
|--------|-------------|
| `group` | Group identifier (same for replicates) |
| `replicate` | Integer replicate number |
| `fastq_1`, `fastq_2` | Gzipped FASTQ paths |
| `control` | Control group name (blank for controls themselves) |

IgG controls are auto-matched by replicate number.

### Normalization Modes

| Mode | Description |
|------|-------------|
| `Spikein` (default) | Normalize by E. coli spike-in DNA |
| `RPKM` | Reads Per Kilobase per Million |
| `CPM` | Counts Per Million |
| `BPM` | Bins Per Million |
| `None` | Disable normalization |

### Workflow Steps

1. FastQC → Trim Galore! → Dual alignment (Bowtie2 to target AND spike-in genomes)
2. BAM processing → Duplicate marking (Picard)
3. BedGraph + BigWig generation
4. Peak calling (SEACR and/or MACS2) → Consensus peaks
5. Library complexity (Preseq), Fragment QC (DeepTools)
6. Heatmap analysis (DeepTools), IGV sessions, MultiQC

### Key Parameters

```bash
nextflow run nf-core/cutandrun \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --peakcaller 'SEACR,MACS2' \
  --normalisation_mode Spikein \
  -profile docker
```

| Parameter | Description |
|-----------|-------------|
| `--peakcaller` | `SEACR`, `MACS2`, or `SEACR,MACS2` |
| `--normalisation_mode` | `Spikein`, `RPKM`, `CPM`, `BPM`, `None` |
| `--use_control` | Enable control normalization in peak calling |
| `--remove_mitochondrial_reads` | Filter mitochondrial sequences |
| `--dedup_target_reads` | Deduplicate primary samples |
| `--replicate_threshold` | Minimum replicates for consensus peaks |

### Output Files

- Target and spike-in aligned BAMs
- BigWig normalized coverage tracks
- SEACR and/or MACS2 peak files (BED)
- Consensus peaks, DeepTools heatmap PDFs
- IGV session files, MultiQC

---

## nf-core/methylseq

**DOI**: 10.5281/zenodo.1343417

Pipeline for methylation (bisulfite) sequencing and TAPS data.

### Aligner Options

| Aligner | Flag | Method | Notes |
|---------|------|--------|-------|
| Bismark + Bowtie2 | `--aligner bismark` (default) | Standard bisulfite | Most common |
| Bismark + HISAT2 | `--aligner bismark_hisat` | Splice-aware | For SLAMseq |
| bwa-meth | `--aligner bwameth` | Standard bisulfite | BWA-MEM2 with `--use_mem2` |
| bwa-mem | `--aligner bwamem` | TAPS data | Requires `--taps` flag |

### Samplesheet Format

```csv
sample,fastq_1,fastq_2,genome
SAMPLE_1,/data/s1_R1.fastq.gz,/data/s1_R2.fastq.gz,
SAMPLE_2,/data/s2_R1.fastq.gz,,
```

Single-end: omit `fastq_2`. Identical sample names trigger concatenation.

### Workflow Steps

**Bismark workflow**: Index → FastQ merge → QC → Trim → Align → Dedup → Methylation extraction → Reports

**bwa-meth workflow**: Index → FastQ merge → QC → Trim → Align → Dedup (Picard) → MethylDackel extraction

**TAPS workflow**: Index → QC → Trim → Align (bwa mem) → Dedup → rastair conversion

### Key Parameters

```bash
# Standard bisulfite
nextflow run nf-core/methylseq \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  -profile docker

# TAPS analysis
nextflow run nf-core/methylseq \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --aligner bwamem \
  --taps \
  -profile docker

# GPU-accelerated bwa-meth
nextflow run nf-core/methylseq \
  --input samplesheet.csv \
  --aligner bwameth \
  -profile gpu
```

| Parameter | Description |
|-----------|-------------|
| `--aligner` | `bismark`, `bismark_hisat`, `bwameth`, `bwamem` |
| `--taps` | Enable TAPS processing |
| `--use_mem2` | Use BWA-MEM2 for faster alignment |
| `--run_targeted_sequencing` | Enable targeted region analysis |
| `--target_regions_file` | BED file for targeted regions |
| `--dedup` | Enable duplicate removal |
| `--save_intermediates` | Keep trimmed reads and alignment files |

### Output Files

- Aligned BAM files with indices
- Methylation call files (Bismark coverage or MethylDackel bedGraph)
- Bismark sample/summary reports or Picard MarkDuplicates reports
- FastQC, Qualimap, Preseq QC
- MultiQC aggregated report

---

## Pipeline Selection Guide

```
What type of epigenomic data do you have?
  ├─ Open chromatin (ATAC-seq) → nf-core/atacseq
  ├─ Histone marks or TF binding (ChIP-seq) → nf-core/chipseq
  ├─ CUT&RUN / CUT&Tag / TIPseq → nf-core/cutandrun
  └─ DNA methylation
       ├─ Bisulfite sequencing → nf-core/methylseq (--aligner bismark)
       └─ TAPS → nf-core/methylseq (--aligner bwamem --taps)
```

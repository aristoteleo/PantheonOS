---
id: nfcore_variant_calling
name: "nf-core: Variant Calling Pipeline (Sarek)"
description: |
  nf-core/sarek pipeline for germline and somatic variant detection from WGS,
  WES, or targeted sequencing with 16+ variant callers.
tags:
  - nf-core
  - variant calling
  - WGS
  - WES
  - somatic
  - germline
  - sarek
  - GATK
---

# nf-core: Variant Calling Pipeline (Sarek)

**Latest version**: 3.7.x | **DOI**: 10.5281/zenodo.3476425

## Overview

nf-core/sarek detects **germline and somatic variants** (SNVs, indels, SVs,
CNVs, MSI) from WGS, WES, or targeted sequencing. It handles tumor/normal
pairs, tumor-only samples, and multiple relapses. Works with any species
having a reference genome.

### Key Features

- 6 pipeline entry points (mapping → annotation)
- 16+ variant callers
- GPU acceleration via NVIDIA Parabricks (experimental)
- UMI support (fgbio)
- Multi-caller consensus
- SnpEff/VEP/BCFtools annotation

---

## Pipeline Steps

The pipeline has 6 entry points via `--step`:

| Step | Flag | Input | Description |
|------|------|-------|-------------|
| Mapping | `--step mapping` (default) | FASTQ | Align reads to reference |
| Mark duplicates | `--step markduplicates` | BAM | Mark duplicate reads |
| Prepare recalibration | `--step prepare_recalibration` | BAM | Generate BQSR tables |
| Recalibrate | `--step recalibrate` | BAM + table | Apply BQSR |
| Variant calling | `--step variant_calling` | Recalibrated BAM | Run variant callers |
| Annotate | `--step annotate` | VCF | Annotate variants |

---

## Samplesheet Format

```csv
patient,sex,status,sample,lane,fastq_1,fastq_2
PATIENT_1,XX,0,NORMAL,lane1,/data/normal_R1.fastq.gz,/data/normal_R2.fastq.gz
PATIENT_1,XX,1,TUMOR,lane1,/data/tumor_R1.fastq.gz,/data/tumor_R2.fastq.gz
```

| Column | Required | Description |
|--------|----------|-------------|
| `patient` | Yes | Patient/subject identifier |
| `sex` | No | `XX` or `XY` (default: NA) |
| `status` | No | `0` = normal, `1` = tumor (default: 0) |
| `sample` | Yes | Unique sample ID |
| `lane` | Yes (mapping) | Lane identifier |
| `fastq_1`, `fastq_2` | Yes (mapping) | Gzipped FASTQ files |
| `bam`, `bai` | For downstream steps | BAM + index |
| `cram`, `crai` | For downstream steps | CRAM + index |
| `table` | For recalibrate step | Recalibration table |
| `vcf` | For annotate step | VCF file |
| `contamination` | No | Tumor contamination fraction for Varlociraptor |

---

## Supported Variant Callers

### SNV / Indel Callers

| Tool | Germline | Tumor-Only | Somatic | WGS | WES | Panel |
|------|:--------:|:----------:|:-------:|:---:|:---:|:-----:|
| DeepVariant | Yes | — | — | Yes | Yes | Yes |
| FreeBayes | Yes | Yes | Yes | Yes | Yes | Yes |
| GATK HaplotypeCaller | Yes | — | — | Yes | Yes | Yes |
| GATK Mutect2 | — | Yes | Yes | Yes | Yes | Yes |
| Lofreq | — | Yes | — | Yes | Yes | Yes |
| mpileup | Yes | Yes | — | Yes | Yes | Yes |
| MuSE | — | — | Yes | Yes | Yes | Yes |
| Strelka | — | — | Yes | Yes | Yes | — |

### Structural Variant Callers

| Tool | Germline | Tumor-Only | Somatic | WGS | WES | Panel |
|------|:--------:|:----------:|:-------:|:---:|:---:|:-----:|
| Manta | Yes | Yes | Yes | Yes | Yes | Yes |
| TIDDIT | Yes | Yes | Yes | Yes | Yes | Yes |

### Copy Number Callers

| Tool | Germline | Tumor-Only | Somatic | WGS | WES | Panel |
|------|:--------:|:----------:|:-------:|:---:|:---:|:-----:|
| indexcov | Yes | — | Yes | Yes | — | — |
| ASCAT | — | — | Yes | Yes | Yes | — |
| CNVKit | Yes | Yes | Yes | Yes | Yes | — |
| Control-FREEC | — | Yes | Yes | Yes | Yes | Yes |

### Microsatellite Instability

| Tool | Tumor-Only | Somatic | WGS | WES | Panel |
|------|:----------:|:-------:|:---:|:---:|:-----:|
| MSIsensor2 | Yes | — | Yes | Yes | Yes |
| MSIsensorPro | — | Yes | Yes | Yes | Yes |

### Meta-Caller

| Tool | Germline | Tumor-Only | Somatic | Description |
|------|:--------:|:----------:|:-------:|-------------|
| Varlociraptor | Yes | Yes | Yes | Posterior probability-based filtering |

---

## Aligners

| Aligner | Description |
|---------|-------------|
| `bwa-mem` (default) | Standard BWA-MEM |
| `bwa-mem2` | Faster BWA-MEM2 |
| `dragmap` | DRAGEN mapper |
| `sentieon` | Commercial accelerated aligner |
| `parabricks` | NVIDIA GPU-accelerated (experimental) |

---

## Usage Examples

### Germline Variant Calling (WGS)

```bash
nextflow run nf-core/sarek \
  --input samplesheet.csv \
  --outdir results \
  --genome GATK.GRCh38 \
  --tools haplotypecaller,deepvariant,manta,tiddit \
  -profile docker
```

### Somatic Variant Calling (Tumor/Normal)

```bash
nextflow run nf-core/sarek \
  --input samplesheet.csv \
  --outdir results \
  --genome GATK.GRCh38 \
  --tools mutect2,strelka,manta,ascat,cnvkit,msisensorpro \
  -profile docker
```

### Tumor-Only Analysis

```bash
nextflow run nf-core/sarek \
  --input samplesheet.csv \
  --outdir results \
  --genome GATK.GRCh38 \
  --tools mutect2,freebayes,manta,cnvkit,msisensor2 \
  -profile docker
```

### Variant Annotation Only

```bash
nextflow run nf-core/sarek \
  --step annotate \
  --input samplesheet.csv \
  --outdir results \
  --genome GATK.GRCh38 \
  --tools snpeff,vep \
  -profile docker
```

---

## Key Parameters

| Parameter | Description |
|-----------|-------------|
| `--input` | Samplesheet CSV |
| `--outdir` | Output directory |
| `--genome` | Reference genome (e.g., `GATK.GRCh38`) |
| `--step` | Pipeline entry point: `mapping`, `markduplicates`, `prepare_recalibration`, `recalibrate`, `variant_calling`, `annotate` |
| `--tools` | Comma-separated variant callers and annotators |
| `--aligner` | `bwa-mem` (default), `bwa-mem2`, `dragmap`, `sentieon`, `parabricks` |
| `--trim_fastq` | Enable quality trimming (fastp) |
| `--handle_umi` | Enable UMI processing (fgbio) |
| `--split_fastq` | Chunk size for parallelization |
| `--save_mapped` | Save intermediate mapping results |

---

## Post-Variant Calling

- **Filtering**: BCFtools PASS filtering on all VCFs
- **Normalization**: BCFtools norm
- **Consensus**: Minimum 2-tool agreement (`-n+2`, adjustable)
- **Germline concatenation**: BCFtools concat across callers
- **Varlociraptor**: Posterior probability-based filtering with custom scenarios

---

## Output Files

| Directory | Contents |
|-----------|----------|
| `preprocessing/mapped/` | Aligned BAMs |
| `preprocessing/markduplicates/` | Deduplicated BAMs |
| `preprocessing/recalibrated/` | Recalibrated BAMs/CRAMs |
| `variant_calling/<tool>/<sample>/` | Per-caller VCF + tabix index |
| `variant_calling/consensus/` | Multi-caller consensus variants |
| `annotation/<sample>/` | SnpEff/VEP annotated VCFs |
| `reports/` | FastQC, fastp, mosdepth coverage, samtools stats |
| `multiqc/` | Aggregated MultiQC report |
| CSV manifests | `mapped.csv`, `markduplicates.csv`, `recalibrated.csv`, `variantcalled.csv` |

---

## Caller Selection Guide

```
What type of analysis?
  ├─ Germline only
  │    └─ --tools haplotypecaller,deepvariant,manta,tiddit,cnvkit
  ├─ Somatic (tumor/normal pair)
  │    └─ --tools mutect2,strelka,manta,ascat,cnvkit,msisensorpro
  ├─ Tumor-only (no matched normal)
  │    └─ --tools mutect2,freebayes,manta,cnvkit,msisensor2
  └─ Annotation only
       └─ --step annotate --tools snpeff,vep
```

> [!TIP]
> For somatic analysis, running multiple callers and using consensus
> (`variant_calling/consensus/`) improves specificity. The default
> requires agreement from at least 2 callers.

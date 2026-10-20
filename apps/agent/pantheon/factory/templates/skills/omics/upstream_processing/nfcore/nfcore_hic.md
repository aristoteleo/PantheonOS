---
id: nfcore_hic
name: "nf-core: Hi-C Chromatin Conformation Pipeline"
description: |
  nf-core/hic pipeline for processing Hi-C chromosome conformation capture
  data, from raw reads to contact maps, TADs, and A/B compartments.
tags:
  - nf-core
  - Hi-C
  - chromatin
  - 3D genome
  - TAD
  - compartments
  - contact maps
  - HiC-Pro
  - cooler
---

# nf-core: Hi-C Chromatin Conformation Pipeline

**Latest version**: 2.1.0 | **DOI**: 10.5281/zenodo.2669512

## Overview

nf-core/hic processes **Hi-C** (chromosome conformation capture) data to
study 3D genome organization. It produces multi-resolution contact maps,
calls TADs and A/B compartments, and generates distance decay profiles.

### Supported Protocols

| Protocol | Flag | Description |
|----------|------|-------------|
| **Digestion Hi-C** | `--digestion <enzyme>` | Standard restriction enzyme-based Hi-C |
| **DNase Hi-C** | `--dnase` | Enzyme-free protocol |

### Restriction Enzyme Presets

| Preset | Enzyme | Restriction site | Ligation site |
|--------|--------|-------------------|---------------|
| `hindiii` | HindIII | `A^AGCTT` | `AAGCTAGCTT` |
| `mboi` | MboI | `^GATC` | `GATCGATC` |
| `dpnii` | DpnII | `^GATC` | `GATCGATC` |
| `arima` | Arima kit | `^GATC,G^ANTC` | `GATCGATC,GANTGATC,GANTANTC,GATCANTC` |

Custom enzymes: use `--restriction_site` and `--ligation_site` directly.

---

## Workflow Steps

1. **Read QC** — FastQC
2. **Two-step mapping** — Bowtie2 via HiC-Pro strategy (end-to-end alignment,
   then trim at ligation site and re-align chimeric reads)
3. **Valid pairs detection** — Classify read pairs (valid interactions,
   dangling ends, self-circles, religation)
4. **Duplicate removal**
5. **Contact map generation** — cooler at multiple resolutions
6. **Normalization** — ICE (HiC-Pro) and/or cooler balancing
7. **Distance decay analysis** — HiCExplorer
8. **TAD calling** — HiCExplorer and/or cooltools insulation score
9. **Compartment calling** — cooltools eigenvector decomposition (A/B compartments)
10. **MultiQC reporting**

### Tools Used

| Tool | Purpose |
|------|---------|
| Bowtie2 | Read alignment (two-step mapping) |
| HiC-Pro | Mapping strategy, valid pairs, contact maps, ICE normalization |
| cooler | Contact map storage (.cool/.mcool format) |
| cooltools | Compartment calling, insulation score TAD calling |
| HiCExplorer | TAD calling, distance decay plots, QC |
| FastQC / MultiQC | Quality control and reporting |

---

## Samplesheet Format

```csv
sample,fastq_1,fastq_2
SAMPLE_REP1,/data/S1_L002_R1.fastq.gz,/data/S1_L002_R2.fastq.gz
SAMPLE_REP2,/data/S2_L002_R1.fastq.gz,/data/S2_L002_R2.fastq.gz
```

| Column | Required | Description |
|--------|----------|-------------|
| `sample` | Yes | Sample name (identical names merge across rows) |
| `fastq_1` | Yes | Gzipped forward reads FASTQ |
| `fastq_2` | Yes | Gzipped reverse reads FASTQ |

> [!TIP]
> Hi-C is always paired-end. Multiple sequencing runs of the same sample
> can be merged by using the same sample name across rows.

---

## Usage Examples

### Digestion Hi-C (DpnII, Mouse)

```bash
nextflow run nf-core/hic -r 2.1.0 \
  --input samplesheet.csv \
  --outdir results \
  --genome mm10 \
  --digestion dpnii \
  -profile docker
```

### Digestion Hi-C (Arima Kit, Human)

```bash
nextflow run nf-core/hic -r 2.1.0 \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --digestion arima \
  -profile docker
```

### DNase Hi-C

```bash
nextflow run nf-core/hic -r 2.1.0 \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --dnase \
  --min_cis_dist 1000 \
  -profile docker
```

### Custom Restriction Enzyme

```bash
nextflow run nf-core/hic -r 2.1.0 \
  --input samplesheet.csv \
  --outdir results \
  --fasta /ref/genome.fa \
  --restriction_site '^GATC' \
  --ligation_site 'GATCGATC' \
  --bin_size '1000000,500000,250000,100000' \
  -profile docker
```

---

## Key Parameters

### Input/Output

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--input` | — | Samplesheet CSV (required) |
| `--outdir` | — | Output directory (required) |
| `--genome` | — | iGenomes reference (e.g., `GRCh38`, `mm10`) |
| `--fasta` | — | Custom genome FASTA |
| `--bwt2_index` | — | Pre-built Bowtie2 index |

### Protocol

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--digestion` | — | Enzyme preset: `hindiii`, `mboi`, `dpnii`, `arima` |
| `--restriction_site` | — | Custom restriction motif (e.g., `^GATC`) |
| `--ligation_site` | — | Expected ligation motif |
| `--dnase` | false | Enable DNase Hi-C mode |
| `--min_cis_dist` | — | Minimum cis distance filter (for DNase Hi-C) |

### Alignment

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--min_mapq` | 10 | Minimum mapping quality |
| `--split_fastq` | false | Split FASTQ for parallel processing |
| `--fastq_chunks_size` | 20000000 | Reads per chunk |
| `--bwt2_opts_end2end` | `--very-sensitive -L 30 ...` | Bowtie2 end-to-end options |
| `--bwt2_opts_trimmed` | `--very-sensitive -L 20 ...` | Bowtie2 trimmed reads options |

### Valid Pairs

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--keep_dups` | false | Retain duplicate reads |
| `--keep_multi` | false | Retain multi-mapped reads |
| `--min_insert_size` | — | Minimum insert size filter |
| `--max_insert_size` | — | Maximum insert size filter |
| `--min_restriction_fragment_size` | — | Minimum restriction fragment size |
| `--max_restriction_fragment_size` | — | Maximum restriction fragment size |

### Contact Maps

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--bin_size` | `1000000,500000` | Resolution(s) in bp (comma-separated) |
| `--hicpro_maps` | false | Also generate HiC-Pro format maps |
| `--ice_filter_low_count_perc` | 0.02 | Low-count bin filter for ICE |
| `--ice_max_iter` | 100 | Max ICE iterations |
| `--ice_eps` | 0.1 | ICE convergence threshold |
| `--res_zoomify` | 5000 | Max resolution for mcool zoom |

### Downstream Analysis

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--res_dist_decay` | `1000000` | Resolution(s) for distance decay |
| `--tads_caller` | `hicexplorer,insulation` | TAD caller(s) |
| `--res_tads` | `40000,20000` | Resolution(s) for TAD calling |
| `--res_compartments` | `250000` | Resolution(s) for compartment calling |

### Skip Options

| Parameter | Description |
|-----------|-------------|
| `--skip_maps` | Stop after valid pairs; skip contact maps |
| `--skip_balancing` | Skip cooler normalization |
| `--skip_mcool` | Skip mcool generation |
| `--skip_dist_decay` | Skip distance decay analysis |
| `--skip_tads` | Skip TAD calling |
| `--skip_compartments` | Skip compartment calling |
| `--skip_multiqc` | Skip MultiQC report |

---

## Output Files

### Alignment & Valid Pairs

| Directory | Contents |
|-----------|----------|
| `hicpro/mapping/` | Paired BAM files (`*bwt2pairs.bam`), mapping stats |
| `hicpro/valid_pairs/` | `*.validPairs` (interactions), `*.DEpairs` (dangling ends), `*.SCPairs` (self-circles), `*.REPairs` (religation), `*allValidPairs` (merged, deduplicated) |
| `hicpro/valid_pairs/pairix/` | 4DN-standard pairs files (compressed, indexed) |
| `hicpro/stats/` | Mapping, pairing, classification, and merge statistics |

### Contact Maps

| Directory | Contents |
|-----------|----------|
| `contact_maps/raw/` | Raw contact maps (`.txt`, `.cool`) at each resolution |
| `contact_maps/norm/` | Normalized contact maps (`.txt`, `.cool`, `.mcool`) |
| `contact_maps/bins/` | Genomic bin coordinate files |
| `hicpro/matrix/` | HiC-Pro format maps (`*.matrix`, `*_iced.matrix`) if `--hicpro_maps` |

### Downstream Analysis

| Directory | Contents |
|-----------|----------|
| `dist_decay/` | Contact frequency vs. distance plots and tables |
| `compartments/` | Eigenvector files (`*cis.vecs.tsv`), eigenvalues (`*cis.lam.txt`) |
| `tads/insulation/` | Insulation score profiles, boundary positions (cooltools) |
| `tads/hicexplorer/` | TAD boundaries (BED), boundary scores (bigWig) |

### Reports

| Directory | Contents |
|-----------|----------|
| `multiqc/` | `multiqc_report.html`, parsed data, static plots |
| `pipeline_info/` | Execution report, timeline, trace, DAG, software versions |

---

## Expected QC Metrics

| Metric | Typical range | Description |
|--------|--------------|-------------|
| Aligned reads | 80–90% | Overall mapping rate |
| Step-2 aligned | ~10% | Chimeric reads at ligation junctions |
| Valid pairs | 40–60% | Usable Hi-C interactions |
| Cis interactions | >60% | Same-chromosome contacts (should dominate) |
| Long-range cis (>20kb) | >15% | Informative long-range contacts |
| Trans interactions | <40% | Inter-chromosomal contacts |
| Duplicates | <20% | PCR duplicate rate |

> [!WARNING]
> High trans interaction rates (>50%) or low valid pair percentages (<30%)
> indicate potential library quality issues.

---

## Connecting to Downstream Analysis

### Load Contact Maps in Python

```python
import cooler

# Load single-resolution cool file
clr = cooler.Cooler("results/contact_maps/norm/sample_500000.cool")
matrix = clr.matrix(balance=True).fetch("chr1")

# Load multi-resolution mcool file
clr = cooler.Cooler("results/contact_maps/norm/sample.mcool::resolutions/50000")
```

### Visualize with HiGlass

The `.mcool` output files are directly compatible with
[HiGlass](https://higlass.io/) for interactive visualization.

### Further Analysis with cooltools

```python
import cooltools

# Compute expected contact frequency
expected = cooltools.expected_cis(clr, nproc=4)

# Call compartments
eigvals, eigvecs = cooltools.eigs_cis(clr, n_eigs=3, phasing_track=gc_track)

# Compute insulation score
insulation = cooltools.insulation(clr, window_bp=[100000, 200000])
```

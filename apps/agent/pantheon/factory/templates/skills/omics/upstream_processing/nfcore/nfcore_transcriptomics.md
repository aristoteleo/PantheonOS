---
id: nfcore_transcriptomics
name: "nf-core: Single-Cell & Bulk RNA-seq Pipelines"
description: |
  nf-core pipelines for processing single-cell RNA-seq (scrnaseq, scdownstream)
  and bulk RNA-seq (rnaseq) data from raw FASTQs to count matrices.
tags:
  - nf-core
  - scRNA-seq
  - RNA-seq
  - 10x
  - STARsolo
  - Salmon
  - CellRanger
---

# nf-core: Single-Cell & Bulk RNA-seq Pipelines

## Pipeline Overview

| Pipeline | Assay | Aligners | Key Output |
|----------|-------|----------|------------|
| **scrnaseq** | scRNA-seq | Simpleaf, STARsolo, Kallisto, CellRanger | Count matrices (h5ad, rds, mtx) |
| **scdownstream** | scRNA-seq (post-quant) | N/A | Integrated h5ad with annotations |
| **rnaseq** | Bulk RNA-seq | STAR, HISAT2, Salmon, Kallisto | Gene/transcript counts, TPM |

---

## nf-core/scrnaseq

**Latest version**: 4.1.0 | **DOI**: 10.5281/zenodo.3568187

Best-practice pipeline for processing single-cell RNA sequencing data
from barcode-based protocols.

### Supported Protocols

| Protocol | CellRanger | Simpleaf | STARsolo | Kallisto | CellRanger-arc |
|----------|:----------:|:--------:|:--------:|:--------:|:--------------:|
| 10x V1 (`10XV1`) | Yes | Yes | Yes | Yes | No |
| 10x V2 (`10XV2`) | Yes | Yes | Yes | Yes | No |
| 10x V3 (`10XV3`) | Yes | Yes | Yes | Yes | No |
| 10x V4 (`10XV4`) | Yes | Yes | Yes | Yes | No |
| Drop-seq (`dropseq`) | No | Yes | Yes | Yes | No |
| Smart-seq3 (`smartseq`) | No | No | Yes | Yes | No |
| auto | Yes | No | No | No | Yes |

### Aligner Options

- **`simpleaf`** (default): piscem pseudo-alignment + Alevin-fry quantification + AlevinQC
- **`kallisto`**: Kallisto alignment + bustools downstream processing
- **`star`**: STARsolo unified alignment and quantification
- **`cellranger`**: 10x Genomics Cell Ranger (community-maintained container)
- **`cellranger-multi`**: Multi-feature type processing (GEX, V(D)J, antibodies, CRISPR, CMO)
- **`cellranger-arc`**: Multiome (ATAC + GEX) processing

### Samplesheet Format

```csv
sample,fastq_1,fastq_2,expected_cells
SAMPLE_1,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz,5000
SAMPLE_2,/data/S2_R1.fastq.gz,/data/S2_R2.fastq.gz,5000
```

| Column | Required | Description |
|--------|----------|-------------|
| `sample` | Yes | Sample ID (identical names trigger read concatenation) |
| `fastq_1` | Yes | Gzipped R1 FASTQ path |
| `fastq_2` | Yes | Gzipped R2 FASTQ path |
| `expected_cells` | No | Expected cell count |
| `seq_center` | No | Sequencing center (STARsolo BAM only) |

For **cellranger-multi**: add `feature_type` column (`gex`, `vdj`, `ab`, `crispr`, `cmo`).
For **cellranger-arc**: add `sample_type` (`atac`/`gex`) and `fastq_barcode` columns.

### Key Parameters

```bash
nextflow run nf-core/scrnaseq \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  --protocol 10XV3 \
  --aligner star \
  -profile docker
```

| Parameter | Description |
|-----------|-------------|
| `--input` | Samplesheet CSV path |
| `--fasta` | Reference genome FASTA |
| `--gtf` | Gene annotation GTF |
| `--protocol` | Barcode kit: `10XV2`, `10XV3`, `dropseq`, `smartseq`, `auto` |
| `--aligner` | `simpleaf` (default), `kallisto`, `star`, `cellranger`, `cellranger-multi`, `cellranger-arc` |
| `--genome` | iGenomes reference name (e.g., `GRCh38`) |
| `--cellranger_index` | Pre-built CellRanger index |

### Output Files

| Directory | Contents |
|-----------|----------|
| `results/fastqc/` | HTML QC reports per sample |
| `results/<aligner>/` | Alignment outputs (BAM, count matrices, metrics) |
| `results/<aligner>/mtx_conversions/` | `.h5ad` (AnnData), `.rds` (Seurat), combined matrix |
| `results/cellbender/` | Empty-droplet filtered matrices (if enabled) |
| `multiqc/` | Aggregated MultiQC report |

---

## nf-core/scdownstream

**Status**: Development version

Pipeline for **post-quantification** single-cell RNA-seq analysis. Accepts
pre-quantified data (h5ad, h5, RDS, CSV).

### Workflow Stages

1. **Per-sample preprocessing**:
   - Format conversion (RDS → h5ad)
   - Ambient RNA removal: decontX, soupX, cellbender, scAR
   - Doublet detection: SOLO, scrublet, DoubletDetection, SCDS, majority voting
2. **Sample aggregation**: Merge into unified h5ad
3. **Integration & batch correction**: scVI, scANVI, Harmony, BBKNN, Combat, Seurat
4. **Annotation & analysis**: celltypist annotation, Leiden clustering, UMAP
5. **Output**: Integrated h5ad, SingleCellExperiment, QC reports

### Samplesheet Format

```csv
sample,unfiltered
SAMPLE_1,/data/sample1.h5ad
SAMPLE_2,/data/sample2.h5ad
```

### Usage

```bash
nextflow run nf-core/scdownstream \
  --input samplesheet.csv \
  --outdir results \
  -profile docker
```

---

## nf-core/rnaseq

**Latest version**: 3.22.2 | **DOI**: 10.5281/zenodo.1400710

Best-practice pipeline for bulk RNA sequencing analysis with automatic
strandedness detection, multiple alignment routes, and comprehensive QC.

### Alignment Routes

| Route | Aligner | Quantifier | Notes |
|-------|---------|------------|-------|
| `star_salmon` (default) | STAR | Salmon | Recommended; gene + transcript quantification |
| `star_rsem` | STAR | RSEM | Alternative quantification |
| `hisat2` | HISAT2 | — | Alignment only, no quantification |
| — | Salmon | Salmon | Pseudo-alignment (optional, via `--pseudo_aligner`) |
| — | Kallisto | Kallisto | Pseudo-alignment (optional, via `--pseudo_aligner`) |

### Samplesheet Format

```csv
sample,fastq_1,fastq_2,strandedness
CONTROL_1,/data/ctrl1_R1.fastq.gz,/data/ctrl1_R2.fastq.gz,auto
CONTROL_2,/data/ctrl2_R1.fastq.gz,/data/ctrl2_R2.fastq.gz,auto
TREATED_1,/data/treat1_R1.fastq.gz,/data/treat1_R2.fastq.gz,auto
```

| Column | Description |
|--------|-------------|
| `sample` | Sample ID (identical names merge technical replicates) |
| `fastq_1` | Forward-strand FASTQ |
| `fastq_2` | Reverse-strand FASTQ (paired-end) |
| `strandedness` | `auto`, `forward`, `reverse`, or `unstranded` |

### Workflow Steps (16 Stages)

1. Merge re-sequenced FastQ files
2. Auto-infer strandedness (Salmon subsampling)
3. Read QC (FastQC)
4. UMI extraction (UMI-tools, optional)
5. Adapter/quality trimming (Trim Galore! or fastp)
6. Genome contaminant removal (BBSplit, optional)
7. Ribosomal RNA removal (SortMeRNA, optional)
8. Alignment (STAR/HISAT2)
9. Alignment processing (SAMtools sort/index)
10. UMI-based deduplication
11. Duplicate marking (Picard MarkDuplicates)
12. Transcript assembly (StringTie)
13. Coverage visualization (bigWig)
14. Comprehensive QC (RSeQC, Qualimap, dupRadar, Preseq, featureCounts, DESeq2)
15. Optional pseudo-alignment (Salmon/Kallisto)
16. Report generation (MultiQC)

### Key Parameters

```bash
nextflow run nf-core/rnaseq \
  --input samplesheet.csv \
  --outdir results \
  --fasta genome.fasta \
  --gtf reference.gtf \
  --aligner star_salmon \
  -profile docker
```

| Parameter | Description |
|-----------|-------------|
| `--aligner` | `star_salmon` (default), `star_rsem`, `hisat2` |
| `--pseudo_aligner` | `salmon` or `kallisto` (optional additional quantification) |
| `--skip_alignment` | Skip alignment (use with BAM input) |
| `--save_reference` | Save generated indices for reuse |
| `--save_trimmed` | Save trimmed reads |
| `--save_unaligned` | Save unmapped reads |

### Output Files

**Count matrices (STAR+Salmon):**
- `salmon.merged.gene_counts.tsv` — raw gene counts
- `salmon.merged.gene_tpm.tsv` — TPM-normalized gene expression
- `salmon.merged.gene_counts_scaled.tsv` — library-size scaled counts
- `salmon.merged.transcript_counts.tsv` — transcript-level counts
- `.SummarizedExperiment.rds` — R object for downstream analysis

**QC outputs:**
- RSeQC (strandedness, read distribution, junction saturation, duplication)
- Qualimap alignment quality
- dupRadar (duplication vs expression)
- Preseq (library complexity)
- DESeq2 (PCA, clustering, size factors)
- BigWig coverage tracks
- MultiQC aggregated report

---

## Connecting to Downstream Analysis

After running nf-core/scrnaseq, the output `.h5ad` files can be loaded
directly into scanpy for downstream analysis:

```python
import scanpy as sc

# Load nf-core/scrnaseq output
adata = sc.read_h5ad("results/star/mtx_conversions/combined_matrix.h5ad")

# Standard preprocessing
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], inplace=True)
```

For bulk RNA-seq, load the count matrix directly:

```python
import pandas as pd
counts = pd.read_csv("results/star_salmon/salmon.merged.gene_counts.tsv", sep="\t", index_col=0)
```

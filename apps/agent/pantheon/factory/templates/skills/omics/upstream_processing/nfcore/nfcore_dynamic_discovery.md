---
id: nfcore_dynamic_discovery
name: "nf-core: Dynamic Pipeline Discovery & Usage"
description: |
  Meta-skill for dynamically discovering and using ANY nf-core pipeline,
  including those not covered by pre-written skill files. Teaches the agent
  how to fetch pipeline documentation, parameters, and samplesheet formats
  on-the-fly from standardized nf-core resources.
tags:
  - nf-core
  - nextflow
  - dynamic
  - discovery
  - pipeline
---

# nf-core: Dynamic Pipeline Discovery & Usage

This skill enables you to **dynamically discover and use any of the 143+
nf-core pipelines** — even those without a pre-written skill file. All
nf-core pipelines follow a standardized structure, making it possible to
fetch and interpret pipeline-specific information on-the-fly.

---

## When to Use This Skill

Use this skill when:

- The user asks about a **pipeline not covered** in the existing skill files
  (nfcore_transcriptomics.md, nfcore_spatial.md, nfcore_epigenomics.md,
  nfcore_variant_calling.md)
- The user wants to **explore available pipelines** for their data type
- You need to look up **specific parameters or samplesheet formats** for
  any nf-core pipeline
- A pipeline has been **updated** and you need the latest parameter info

---

## Step 1: Discover Pipelines

### Option A: CLI (if nf-core tools installed)

```bash
# List all pipelines
nf-core pipelines list

# Filter by keyword
nf-core pipelines list rna
nf-core pipelines list proteomics
nf-core pipelines list metagenomics
```

### Option B: Web Fetch

Fetch the pipeline listing page:

```
URL: https://nf-co.re/pipelines/
```

### Option C: Common Pipeline Categories

| Category | Pipelines |
|----------|-----------|
| Genomics | sarek, raredisease, nanoseq, hlatyping |
| Transcriptomics | rnaseq, scrnaseq, scdownstream, rnafusion, denovotranscript |
| Spatial omics | spatialvi, sopa, molkart, spatialxe |
| Epigenomics | atacseq, chipseq, cutandrun, methylseq, hicar |
| Metagenomics | ampliseq, taxprofiler, mag, eager |
| Proteomics | proteinfold, diaproteomics, proteomicslfq |
| Data fetching | fetchngs, download |
| Multi-omics | scrnaseq (cellranger-arc for multiome) |
| Imaging | mcmicro, molkart |

---

## Step 2: Fetch Pipeline Documentation

All nf-core pipelines follow a **standardized URL structure**. Use WebFetch
to retrieve documentation for any pipeline:

### Documentation URLs

| Page | URL Pattern | What you get |
|------|-------------|-------------|
| **Overview** | `https://nf-co.re/<pipeline>/latest` | Pipeline description, features, workflow diagram |
| **Usage** | `https://nf-co.re/<pipeline>/latest/docs/usage/` | Samplesheet format, required inputs, run examples |
| **Parameters** | `https://nf-co.re/<pipeline>/latest/parameters/` | All parameters with descriptions and defaults |
| **Output** | `https://nf-co.re/<pipeline>/latest/docs/output/` | Output file descriptions and directory structure |

### Example: Fetching info for nf-core/ampliseq

```
WebFetch: https://nf-co.re/ampliseq/latest/docs/usage/
Prompt: "Extract the samplesheet format, required parameters, and usage examples"
```

### Fetching the Parameter Schema

Every nf-core pipeline defines its parameters in a machine-readable JSON schema:

```
URL: https://raw.githubusercontent.com/nf-core/<pipeline>/master/nextflow_schema.json
```

This JSON contains:
- All parameter names, types, and descriptions
- Default values
- Required vs optional flags
- Parameter groupings (input/output, reference genome, alignment options, etc.)

---

## Step 3: Identify Common Patterns

All nf-core pipelines share these patterns — you can rely on them even
before fetching pipeline-specific docs:

### Universal Parameters

Every nf-core pipeline accepts:

```bash
--input <samplesheet.csv>    # Input samplesheet (CSV format)
--outdir <path>              # Output directory
-profile <engine>            # Container engine: docker, singularity, conda
-r <version>                 # Pipeline version for reproducibility
-resume                      # Resume from cached results
--email <address>            # Completion notification
```

### Universal Samplesheet Pattern

All samplesheets are CSV files. While columns vary by pipeline, the
general pattern is:

```csv
sample,fastq_1,fastq_2,...pipeline_specific_columns...
SAMPLE_ID,/path/to/R1.fastq.gz,/path/to/R2.fastq.gz,...
```

Common column patterns:
- `sample` — Sample identifier (almost always present)
- `fastq_1`, `fastq_2` — Sequencing reads (for FASTQ-based pipelines)
- `strandedness` — Library strandedness (RNA-seq pipelines)
- `replicate` — Replicate number (epigenomics pipelines)
- `control` — Control sample reference (ChIP/ATAC/CUT&Run)

### Universal Test Command

Always test before running with real data:

```bash
nextflow run nf-core/<pipeline> -profile test,docker --outdir test_results
```

### Universal Output Structure

```
results/
  <tool_name>/           # Per-tool output directories
  multiqc/
    multiqc_report.html  # Aggregated QC report (almost always present)
  pipeline_info/         # Execution reports, DAG, trace
```

---

## Step 4: Construct the Run Command

Once you have the pipeline-specific information, construct the command
following this template:

```bash
nextflow run nf-core/<pipeline> \
  -r <version> \
  -profile <docker|singularity|conda> \
  --input samplesheet.csv \
  --outdir results \
  <pipeline-specific-params> \
  --genome <reference>  # If applicable
```

### Reference Genomes

Many pipelines accept `--genome` for iGenomes references:

| Genome | Species | Notes |
|--------|---------|-------|
| `GRCh38` | Human | Recommended for most human analyses |
| `GRCh37` | Human | Legacy, some older datasets |
| `GATK.GRCh38` | Human | GATK bundle version (sarek) |
| `GRCm39` | Mouse | Latest mouse assembly |
| `GRCm38` | Mouse | Previous mouse assembly |

Or provide custom references with `--fasta` and `--gtf`.

---

## Step 5: Dynamic Information Retrieval Workflow

When a user asks about a pipeline you don't have pre-written knowledge of,
follow this workflow:

```
1. Identify the pipeline name
   └─ Search: nf-core pipelines list <keyword>
   └─ Or: WebFetch https://nf-co.re/pipelines/

2. Fetch usage documentation
   └─ WebFetch https://nf-co.re/<pipeline>/latest/docs/usage/
   └─ Extract: samplesheet format, required inputs, run examples

3. Fetch parameter documentation (if user needs specific params)
   └─ WebFetch https://nf-co.re/<pipeline>/latest/parameters/
   └─ Extract: parameter names, defaults, descriptions

4. Fetch output documentation (if user wants to know what they'll get)
   └─ WebFetch https://nf-co.re/<pipeline>/latest/docs/output/
   └─ Extract: output files, directory structure

5. Construct and provide:
   - Samplesheet template (CSV)
   - Run command with key parameters
   - Expected output description
```

---

## Example: Dynamically Helping with nf-core/ampliseq

User asks: "I have 16S amplicon sequencing data, how do I process it?"

**Agent workflow:**

1. Identify: 16S → nf-core/ampliseq
2. Fetch: `WebFetch https://nf-co.re/ampliseq/latest/docs/usage/`
3. Extract samplesheet format and key parameters
4. Provide:

```csv
sampleID,forwardReads,reverseReads
SAMPLE_1,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz
SAMPLE_2,/data/S2_R1.fastq.gz,/data/S2_R2.fastq.gz
```

```bash
nextflow run nf-core/ampliseq \
  -r 2.11.0 \
  -profile docker \
  --input samplesheet.csv \
  --outdir results \
  --FW_primer "GTGYCAGCMGCCGCGGTAA" \
  --RV_primer "GGACTACNVGGGTWTCTAAT"
```

---

## Frequently Needed Pipelines (Quick Reference)

These are commonly requested pipelines not in the detailed skill files:

| Pipeline | Assay | Quick start |
|----------|-------|-------------|
| `fetchngs` | Download public data | `--input ids.csv` (SRA/ENA/GEO accessions) |
| `ampliseq` | 16S/ITS amplicon | `--input samplesheet.csv --FW_primer X --RV_primer Y` |
| `taxprofiler` | Metagenomic profiling | `--input samplesheet.csv --databases databases.csv` |
| `mag` | Metagenome assembly | `--input samplesheet.csv --outdir results` |
| `rnafusion` | RNA fusion detection | `--input samplesheet.csv --genome GRCh38` |
| `proteinfold` | Protein structure | `--input samplesheet.csv --mode alphafold2` |
| `nanoseq` | Nanopore sequencing | `--input samplesheet.csv --protocol DNA` |
| `raredisease` | Rare disease WGS/WES | `--input samplesheet.csv --genome GATK.GRCh38` |
| `eager` | Ancient DNA | `--input samplesheet.tsv --genome GRCh38` |
| `hicar` | HiC / chromatin conformation | `--input samplesheet.csv --genome GRCh38` |
| `smrnaseq` | Small RNA-seq (miRNA) | `--input samplesheet.csv --genome GRCh38` |
| `circdna` | Circular DNA detection | `--input samplesheet.csv --outdir results` |

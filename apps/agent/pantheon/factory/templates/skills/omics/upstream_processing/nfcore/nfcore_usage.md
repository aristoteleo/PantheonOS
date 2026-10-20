---
id: nfcore_usage
name: "nf-core: Getting Started & Usage"
description: |
  How to install, configure, and run nf-core Nextflow pipelines on local
  machines, HPC clusters, and cloud environments.
tags:
  - nf-core
  - nextflow
  - pipeline
  - hpc
  - installation
  - configuration
---

# nf-core: Getting Started & Usage

## What is nf-core?

[nf-core](https://nf-co.re/) is a community of 143+ curated bioinformatics
pipelines built with [Nextflow](https://www.nextflow.io/). Pipelines are:

- **Portable**: Run on laptops, HPC clusters, and cloud (AWS, Azure, GCP)
- **Reproducible**: Containerized dependencies via Docker, Singularity, or Conda
- **Tested**: Continuous integration with stable, versioned releases
- **Documented**: Comprehensive docs for every pipeline with MultiQC reports

---

## Installation

### Step 1: Install Java

Java v11+ is required:

```bash
java -version   # Must be >= 11
```

### Step 2: Install Nextflow

```bash
# Quick install
curl -s https://get.nextflow.io | bash
chmod +x nextflow
mv nextflow ~/bin/   # Or any directory in $PATH

# Verify
nextflow run hello
```

Alternative via Bioconda:

```bash
conda create --name nf-core python=3.12 nextflow nf-core
conda activate nf-core
```

### Step 3: Install nf-core Tools (Optional but Recommended)

```bash
# Via pip
pip install nf-core

# Via conda
conda install nf-core

# Via Docker
docker run -itv `pwd`:`pwd` -w `pwd` -u $(id -u):$(id -g) nfcore/tools
```

> [!TIP]
> nf-core CLI tools are optional for running pipelines but very helpful for
> listing pipelines, launching with interactive prompts, and downloading
> for offline use.

### Step 4: Install a Container Engine

| Engine | Best for | Root required? |
|--------|----------|---------------|
| Docker | Local / cloud | Yes (or rootless) |
| Singularity | HPC clusters | No |
| Apptainer | HPC clusters | No |
| Podman | Docker alternative | No |
| Conda/Mamba | Fallback only | No |

> [!WARNING]
> Conda lacks reproducibility guarantees. Prefer Docker or Singularity
> for production runs.

---

## Running a Pipeline: 7-Step Workflow

### 1. Select a Pipeline

```bash
# Browse interactively
nf-core pipelines list

# Or visit https://nf-co.re/pipelines/
```

### 2. Test the Pipeline

Always test with a minimal dataset first:

```bash
nextflow run nf-core/<pipeline> -profile test,docker --outdir test_results
```

### 3. Prepare Your Samplesheet

Each pipeline requires a CSV samplesheet. Example for scrnaseq:

```csv
sample,fastq_1,fastq_2,expected_cells
SAMPLE_1,/data/S1_R1.fastq.gz,/data/S1_R2.fastq.gz,5000
SAMPLE_2,/data/S2_R1.fastq.gz,/data/S2_R2.fastq.gz,5000
```

### 4. Run with Real Data

```bash
nextflow run nf-core/<pipeline> \
  -profile docker \
  --input samplesheet.csv \
  --outdir results \
  --genome GRCh38 \
  -r 3.14.0
```

### 5. Monitor Execution

Nextflow prints real-time progress. Use `--email your@email.com` for
completion notifications or `--hook_url` for Slack/Teams webhooks.

### 6. Resume Failed Runs

```bash
# Resume from cached results
nextflow run nf-core/<pipeline> -resume

# Resume a specific previous run
nextflow log                        # List previous runs
nextflow run nf-core/<pipeline> -resume <run-name>
```

### 7. Review Results

Check `results/multiqc/multiqc_report.html` for aggregated QC.

---

## Parameter Conventions

| Prefix | Scope | Example |
|--------|-------|---------|
| `-` (single) | Nextflow core option | `-profile`, `-resume`, `-r` |
| `--` (double) | Pipeline-specific parameter | `--input`, `--genome`, `--outdir` |

### Passing Parameters via File

Use `-params-file` for complex configurations:

```json
{
  "input": "samplesheet.csv",
  "outdir": "results",
  "genome": "GRCh38",
  "aligner": "star_salmon"
}
```

```bash
nextflow run nf-core/rnaseq -profile docker -params-file params.json
```

### Interactive Launch

```bash
nf-core pipelines launch <pipeline>
```

This opens a guided wizard to set parameters and generates a `params.json`.

---

## Configuration

### Configuration Hierarchy (Lowest → Highest Priority)

1. Pipeline defaults (`config/base.config`)
2. User home config (`~/.nextflow/config`)
3. Working directory config (`nextflow.config`)
4. Command-line `-c` files (multiple allowed)
5. Command-line `--parameter` flags

### HPC Executor Configuration

```groovy
// nextflow.config
process {
  executor = 'slurm'    // Options: slurm, sge, lsf, pbs, aws_batch, google-batch
}
```

### Resource Limits

For Nextflow 24.04.0+ with nf-core template v3.0.0+:

```groovy
process {
  resourceLimits = [
    cpus: 32,
    memory: 256.GB,
    time: 24.h
  ]
}
```

For older pipelines:

```bash
nextflow run nf-core/<pipeline> --max_cpus 32 --max_memory 256.GB --max_time 24.h
```

### Process-Specific Overrides

Override resources for a specific tool:

```groovy
process {
  withName: STAR_ALIGN {
    cpus   = { 32 * task.attempt }
    memory = { 100.GB * task.attempt }
  }
}
```

For tools used multiple times, use the full execution path:

```groovy
process {
  withName: 'NFCORE_RNASEQ:RNASEQ:ALIGN_STAR:STAR_ALIGN' {
    memory = 100.GB
  }
}
```

### Customizing Tool Arguments

Override default tool arguments via `ext.args`:

```groovy
process {
  withName: BOWTIE2_ALIGN {
    ext.args = "-n 0.1"
  }
}
```

### Container Registry

Default: `quay.io`. Override globally:

```groovy
docker.registry = 'myregistry.com'
```

### Institutional Profiles

155+ pre-configured institutional profiles are available:

```bash
# Check if your institution has a profile
nextflow run nf-core/<pipeline> -profile <institution_name>,singularity
```

See [nf-core/configs](https://github.com/nf-core/configs) for the full list.

---

## Singularity on HPC

```groovy
// nextflow.config
singularity {
  enabled    = true
  autoMounts = true
  cacheDir   = '/path/to/singularity/cache'
}
```

Set the cache directory environment variable to avoid re-downloading:

```bash
export NXF_SINGULARITY_CACHEDIR=/shared/singularity/cache
```

---

## Offline Execution

### Download Pipeline and Containers

On an internet-connected machine:

```bash
nf-core pipelines download <pipeline> --container singularity
```

This creates a `.tar.gz` with:

```
<pipeline-download>/
  workflow/       # Pipeline code
  config/         # nf-core/configs
  singularity/    # Container images
```

### Run Offline

Transfer to the offline system and run:

```bash
export NXF_OFFLINE='true'
nextflow run <download_directory>/workflow --input samplesheet.csv --outdir results
```

### Shared Storage Optimization

If the head node has internet but compute nodes do not:

```bash
nf-core pipelines download <pipeline> --singularity-cache-only
export NXF_SINGULARITY_CACHEDIR=/shared/cache
```

---

## nf-core CLI Quick Reference

| Command | Description |
|---------|-------------|
| `nf-core pipelines list` | List all available pipelines |
| `nf-core pipelines launch <pipeline>` | Interactive parameter wizard |
| `nf-core pipelines download <pipeline>` | Download for offline use |
| `nextflow run hello` | Verify Nextflow installation |
| `nextflow log` | List previous run history |
| `nextflow self-update` | Update Nextflow |

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `NXF_VER` | Pin Nextflow version (e.g., `export NXF_VER=23.10.1`) |
| `NXF_OFFLINE` | Set to `'true'` to disable internet access |
| `NXF_SINGULARITY_CACHEDIR` | Shared Singularity image cache path |
| `NXF_EDGE` | Set to `1` to enable edge releases |

---

## Best Practices

1. **Pin pipeline versions** with `-r <version>` for reproducibility
2. **Always test first** with `-profile test,docker` before real data
3. **Use `-resume`** to avoid recomputing successful steps
4. **Never edit pipeline code** directly — use custom config files instead
5. **Use institutional profiles** when available (`-profile <institution>`)
6. **Set `NXF_SINGULARITY_CACHEDIR`** on HPC to share container images
7. **Use `--email`** for completion notifications on long-running jobs
8. **Use params files** (`-params-file`) for complex parameter sets
9. **Check MultiQC reports** for every run to catch QC issues early
10. **Keep Nextflow updated** with `nextflow self-update`

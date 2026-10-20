---
id: sc_bp_reproducibility
name: "SC Best Practices: Reproducibility"
description: |
  Best practices for computational reproducibility in single-cell analysis
  including environment management, workflow tools, and documentation.
tags: [reproducibility, workflow, conda, docker, sc-best-practices]
---

# SC Best Practices: Reproducibility

Best practices for ensuring computational reproducibility in single-cell
genomics analyses, covering environment management, containerization,
workflow orchestration, version control, random seed management, data
sharing, and documentation standards.

**Source**: [https://www.sc-best-practices.org](https://www.sc-best-practices.org)

---

## 1. Why Reproducibility Matters

Single-cell analyses involve long pipelines with many stochastic and
parameter-dependent steps. Without reproducibility practices, results may:

- Change when re-run on the same data (stochastic algorithms)
- Fail to run on different machines (dependency conflicts)
- Be impossible to audit or extend (undocumented choices)
- Not match published figures (version drift)

### Levels of Reproducibility

| Level | Definition | Tools |
|-------|-----------|-------|
| **Repeatable** | Same code, same machine, same result | Random seeds |
| **Reproducible** | Same code, different machine, same result | Containers, environment files |
| **Replicable** | Different code/implementation, same conclusion | Documentation, shared data |

---

## 2. Environment Management

### Conda / Mamba

Conda (or the faster mamba) manages Python/R packages with explicit
version pinning:

```bash
# Create a dedicated environment for the project
conda create -n sc_analysis python=3.10
conda activate sc_analysis

# Install core packages
pip install scanpy==1.9.6 scvi-tools==1.0.4 squidpy==1.3.1
pip install muon==0.1.5 scirpy==0.13.1

# Or install via conda-forge
conda install -c conda-forge scanpy scvi-tools

# Export the exact environment for reproducibility
conda env export > environment.yml

# Recreate the environment on another machine
conda env create -f environment.yml
```

### Pip with Requirements File

```bash
# Generate exact package versions
pip freeze > requirements.txt

# Install from requirements file
pip install -r requirements.txt
```

### Mamba for Speed

```bash
# Mamba is a drop-in replacement for conda with faster dependency resolution
conda install -c conda-forge mamba

# Use mamba instead of conda for all operations
mamba create -n sc_analysis python=3.10
mamba install -c conda-forge scanpy scvi-tools
```

> [!TIP]
> Always create a **project-specific** environment rather than installing
> packages into the base environment. This prevents dependency conflicts
> across projects and makes it easy to export the exact environment state.

### Version Pinning Best Practices

```bash
# GOOD: Pin major versions at minimum
pip install "scanpy>=1.9,<1.10" "scvi-tools>=1.0,<1.1"

# BETTER: Pin exact versions for full reproducibility
pip install scanpy==1.9.6 scvi-tools==1.0.4

# BAD: No version constraints (may break with future updates)
pip install scanpy scvi-tools
```

---

## 3. Containers

### Docker

Docker provides full system-level reproducibility by encapsulating the
operating system, libraries, and code:

```dockerfile
# Dockerfile for single-cell analysis
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy analysis code
COPY . /workspace
WORKDIR /workspace

# Default command
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--allow-root", "--no-browser"]
```

```bash
# Build the container
docker build -t sc_analysis:v1.0 .

# Run analysis in the container
docker run -v /path/to/data:/data -v /path/to/output:/output \
    sc_analysis:v1.0 python analysis.py

# Run interactive Jupyter
docker run -p 8888:8888 -v /path/to/data:/data \
    sc_analysis:v1.0
```

### Singularity / Apptainer

Singularity (now Apptainer) is preferred on HPC clusters where Docker
is not available due to security restrictions:

```bash
# Build Singularity image from Docker image
singularity build sc_analysis.sif docker://sc_analysis:v1.0

# Run on HPC
singularity exec --bind /scratch:/data sc_analysis.sif python analysis.py

# Interactive shell
singularity shell --bind /scratch:/data sc_analysis.sif
```

> [!TIP]
> Use **Docker** for local development and CI/CD pipelines. Use
> **Singularity/Apptainer** for HPC cluster execution. You can build
> Singularity images directly from Docker images, so maintain one Dockerfile
> as the source of truth.

---

## 4. Workflow Managers

### Snakemake

Snakemake (Python-based) defines workflows as rules with input/output
dependencies. It handles parallelization, cluster submission, and
re-execution of only changed steps:

```python
# Snakefile
rule all:
    input:
        "results/clustered.h5ad",
        "results/figures/umap.png"

rule preprocess:
    input:
        "data/raw/filtered_feature_bc_matrix.h5"
    output:
        "results/preprocessed.h5ad"
    conda:
        "envs/scanpy.yaml"
    script:
        "scripts/preprocess.py"

rule cluster:
    input:
        "results/preprocessed.h5ad"
    output:
        "results/clustered.h5ad"
    params:
        resolution=0.5
    conda:
        "envs/scanpy.yaml"
    script:
        "scripts/cluster.py"

rule plot_umap:
    input:
        "results/clustered.h5ad"
    output:
        "results/figures/umap.png"
    conda:
        "envs/scanpy.yaml"
    script:
        "scripts/plot_umap.py"
```

```bash
# Run workflow (local)
snakemake --cores 8 --use-conda

# Run on SLURM cluster
snakemake --cores 100 --use-conda --cluster "sbatch -n {threads} --mem={resources.mem_mb}"

# Dry run (show what would execute)
snakemake -n

# Generate DAG visualization
snakemake --dag | dot -Tpng > dag.png
```

### Nextflow

Nextflow (Groovy-based) is widely used in genomics, especially for
large-scale pipelines. It has strong container support and cloud execution:

```groovy
// main.nf
process PREPROCESS {
    container 'sc_analysis:v1.0'
    input:
        path raw_matrix
    output:
        path "preprocessed.h5ad"
    script:
    """
    python preprocess.py ${raw_matrix} preprocessed.h5ad
    """
}

process CLUSTER {
    container 'sc_analysis:v1.0'
    input:
        path preprocessed
    output:
        path "clustered.h5ad"
    script:
    """
    python cluster.py ${preprocessed} clustered.h5ad --resolution 0.5
    """
}

workflow {
    raw_ch = Channel.fromPath("data/raw/*.h5")
    preprocessed_ch = PREPROCESS(raw_ch)
    CLUSTER(preprocessed_ch)
}
```

```bash
# Run Nextflow pipeline
nextflow run main.nf -with-docker sc_analysis:v1.0

# Resume from last checkpoint
nextflow run main.nf -resume
```

### Workflow Manager Comparison

| Feature | Snakemake | Nextflow |
|---------|-----------|----------|
| Language | Python | Groovy (JVM) |
| Ecosystem | Conda, Singularity, Docker | Docker, Singularity, Conda |
| Cloud support | Limited | Excellent (AWS, GCP, Azure) |
| HPC support | Excellent | Excellent |
| Learning curve | Low (Python users) | Moderate |
| Community | Bioinformatics | Genomics pipelines (nf-core) |

> [!TIP]
> Choose **Snakemake** if your team primarily uses Python and works on HPC.
> Choose **Nextflow** if you need cloud execution or want to leverage the
> extensive nf-core pipeline collection.

---

## 5. Version Control

### Git for Code

```bash
# Initialize repository
git init
git add analysis.py Snakefile environment.yml
git commit -m "Initial single-cell analysis pipeline"

# Use .gitignore for large/generated files
cat > .gitignore << 'EOF'
# Data files (too large for git)
data/raw/
data/processed/
results/*.h5ad

# Jupyter checkpoints
.ipynb_checkpoints/

# Environment
.conda/
__pycache__/

# OS files
.DS_Store
EOF
```

### DVC for Data Version Control

DVC (Data Version Control) tracks large data files alongside code:

```bash
# Initialize DVC
dvc init

# Track large data files
dvc add data/raw/filtered_feature_bc_matrix.h5
dvc add results/processed.h5ad

# Push data to remote storage
dvc remote add -d myremote s3://my-bucket/dvc-store
dvc push

# Reproduce: pull data + run pipeline
dvc pull
dvc repro
```

### Project Structure

```
project/
  |-- data/
  |     |-- raw/                  # Original data (DVC-tracked)
  |     +-- processed/            # Intermediate results (DVC-tracked)
  |-- scripts/
  |     |-- preprocess.py
  |     |-- cluster.py
  |     +-- annotate.py
  |-- notebooks/
  |     |-- 01_qc.ipynb
  |     |-- 02_integration.ipynb
  |     +-- 03_analysis.ipynb
  |-- envs/
  |     +-- scanpy.yaml           # Conda environment spec
  |-- Snakefile                   # Workflow definition
  |-- Dockerfile                  # Container definition
  |-- environment.yml             # Full environment export
  |-- .gitignore
  |-- .dvc/
  +-- README.md
```

---

## 6. Random Seeds

Many single-cell analysis steps involve stochastic algorithms. Setting
random seeds ensures deterministic results:

```python
import numpy as np
import scanpy as sc

# Set global random seeds
SEED = 42
np.random.seed(SEED)
sc.settings.seed = SEED

# For PyTorch-based tools (scvi-tools, etc.)
import torch
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)
    # Note: CUDA operations may still have non-determinism
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

# For scikit-learn
from sklearn.utils import check_random_state
rng = check_random_state(SEED)
```

### Where Seeds Matter

| Step | Stochastic? | How to Set Seed |
|------|-------------|-----------------|
| PCA (truncated SVD) | Yes (randomized) | `sc.pp.pca(adata, random_state=42)` |
| Neighbor graph | Deterministic | N/A (given PCA) |
| UMAP | Yes | `sc.tl.umap(adata, random_state=42)` |
| Leiden clustering | Yes | `sc.tl.leiden(adata, random_state=42)` |
| t-SNE | Yes | `sc.tl.tsne(adata, random_state=42)` |
| scVI/scANVI | Yes (SGD, sampling) | `scvi.settings.seed = 42` |
| Scrublet | Yes (simulation) | `scr.Scrublet(X, random_state=42)` |
| Train/test splits | Yes | `train_test_split(X, random_state=42)` |

> [!WARNING]
> Even with seeds set, results may differ across machines due to floating-point
> arithmetic differences, different BLAS implementations, or GPU non-determinism.
> Containers provide the strongest guarantee of cross-machine reproducibility.

---

## 7. Notebook Best Practices

### Execution Order

```python
# ALWAYS restart kernel and run all cells before sharing
# Jupyter: Kernel -> Restart & Run All
# This catches hidden state dependencies (cells run out of order)
```

### Cell Organization

```python
# Cell 1: Imports and configuration (always first)
import scanpy as sc
import numpy as np
import matplotlib.pyplot as plt

SEED = 42
sc.settings.seed = SEED
sc.settings.figdir = "figures/"
sc.settings.verbosity = 3

# Cell 2: Data loading
adata = sc.read_h5ad("input.h5ad")
print(f"Loaded: {adata.shape}")

# Cell 3+: Analysis steps (one major step per cell)
# ...

# Last cell: Save results
adata.write_h5ad("output.h5ad")
print("Analysis complete.")
```

### Parameterization

```python
# Define all parameters in a single cell at the top of the notebook
# This makes it easy to review and modify analysis choices

PARAMS = {
    'min_genes': 200,
    'max_genes': 6000,
    'max_mt_pct': 20,
    'n_top_genes': 3000,
    'n_pcs': 40,
    'n_neighbors': 15,
    'leiden_resolution': 0.5,
    'seed': 42,
}

# Use throughout the notebook
sc.pp.filter_cells(adata, min_genes=PARAMS['min_genes'])
sc.pp.highly_variable_genes(adata, n_top_genes=PARAMS['n_top_genes'])
sc.tl.leiden(adata, resolution=PARAMS['leiden_resolution'])
```

### Converting Notebooks to Scripts

```bash
# For production pipelines, convert notebooks to scripts
jupyter nbconvert --to script analysis.ipynb

# Or use jupytext for bidirectional notebook-script sync
pip install jupytext
jupytext --to py:percent analysis.ipynb     # Notebook -> script
jupytext --to notebook analysis.py          # Script -> notebook
```

---

## 8. Data Sharing and Standard Formats

### Recommended Formats

| Format | Use Case | Advantages |
|--------|----------|------------|
| **H5AD** | Single-modality scRNA-seq | Standard for scverse; preserves all AnnData slots |
| **H5MU** | Multi-modal data | Extends H5AD for multiple modalities |
| **AnnData on disk** | Very large datasets | Backed mode; reads data lazily |
| **Zarr** | Cloud-native storage | Chunked, parallel I/O; works with AnnData |

```python
# Save processed data with all metadata
adata.write_h5ad("processed.h5ad", compression="gzip")

# For very large datasets, use backed mode
adata = sc.read_h5ad("large_dataset.h5ad", backed='r')

# Save multi-modal data
mdata.write("multimodal.h5mu")
```

### What to Share

| Item | Format | Notes |
|------|--------|-------|
| Raw count matrix | H5AD (`.layers['counts']`) | Before any normalization |
| Processed AnnData | H5AD | Includes embeddings, annotations |
| Code | Git repository | Scripts and/or notebooks |
| Environment | `environment.yml` or Dockerfile | Exact package versions |
| Parameters | JSON/YAML or in `adata.uns` | All analysis choices |

### Depositing Data

```python
# Record analysis provenance in adata.uns
adata.uns['analysis_params'] = {
    'scanpy_version': sc.__version__,
    'normalization': 'total_count_1e4_log1p',
    'n_hvgs': 3000,
    'n_pcs': 40,
    'clustering_resolution': 0.5,
    'seed': 42,
    'date': '2024-01-15',
}

# Save for sharing
adata.write_h5ad("for_submission.h5ad", compression="gzip")
```

---

## 9. Save Intermediate Checkpoints

For large analyses that take hours, save checkpoints after each major step:

```python
# After QC and filtering
adata.write_h5ad("checkpoints/01_qc_filtered.h5ad")

# After normalization and feature selection
adata.write_h5ad("checkpoints/02_normalized.h5ad")

# After batch correction / integration
adata.write_h5ad("checkpoints/03_integrated.h5ad")

# After clustering and annotation
adata.write_h5ad("checkpoints/04_annotated.h5ad")

# Resume from any checkpoint
adata = sc.read_h5ad("checkpoints/03_integrated.h5ad")
```

> [!TIP]
> Checkpoints are especially valuable when downstream analysis involves
> exploratory iteration (testing different resolutions, annotation strategies).
> Restarting from a checkpoint avoids re-running expensive upstream steps
> like integration or batch correction.

---

## 10. Documentation Standards

### Document All Parameter Choices

```python
# BAD: Undocumented parameter choice
sc.tl.leiden(adata, resolution=0.5)

# GOOD: Documented parameter choice with justification
# Resolution 0.5 chosen based on:
# - Clustering stability analysis (clustree) showing stable clusters at 0.3-0.7
# - Visual inspection of UMAP showing over-splitting at resolution > 0.8
# - Known number of expected cell types (~10) in this tissue
sc.tl.leiden(adata, resolution=0.5, key_added='leiden_0.5')
```

### Analysis Log

```python
# Maintain a running log of key decisions
analysis_log = []

analysis_log.append({
    'step': 'QC filtering',
    'cells_before': 15000,
    'cells_after': 12500,
    'filters_applied': 'MT% < 20, genes > 200, genes < 6000',
    'justification': 'Thresholds based on QC metric distributions (see QC plots)',
})

analysis_log.append({
    'step': 'Integration',
    'method': 'scVI',
    'n_latent': 30,
    'batch_key': 'sample_id',
    'justification': 'scVI chosen for its ability to handle confounded batches; '
                     'n_latent=30 based on reconstruction loss plateau',
})

# Save log
import json
with open("analysis_log.json", "w") as f:
    json.dump(analysis_log, f, indent=2)
```

---

## Best Practices Summary

1. **Use dedicated environments**: Create a project-specific conda environment and export it as `environment.yml`.
2. **Pin package versions**: Use exact version pins (`scanpy==1.9.6`) for reproducibility, not loose constraints.
3. **Use containers for cross-machine reproducibility**: Docker for local/CI, Singularity for HPC.
4. **Set random seeds everywhere**: Set seeds for numpy, scanpy, PyTorch, and any stochastic algorithm.
5. **Use workflow managers for multi-step pipelines**: Snakemake (Python) or Nextflow (Groovy) handle dependencies, parallelization, and re-execution.
6. **Version control code with Git**: Track analysis scripts and parameter files. Use DVC for large data files.
7. **Restart-and-run-all**: Before sharing a notebook, always restart the kernel and run all cells to verify execution order independence.
8. **Save intermediate checkpoints**: Write H5AD checkpoints after each major analysis step to enable resumption and iteration.
9. **Document parameter choices**: Record not just what parameters were used, but why they were chosen.
10. **Use standard formats**: H5AD for single-modality, H5MU for multi-modal data. Include raw counts, embeddings, and metadata in the shared file.

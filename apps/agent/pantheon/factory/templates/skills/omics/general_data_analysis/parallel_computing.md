---
id: parallel_computing
name: Parallel Computing for Single-Cell Analysis
description: |
  Performance optimization strategies for single-cell analysis.
  Covers multi-core CPU, GPU acceleration, and memory optimization.
---

# Parallel Computing for Single-Cell Analysis

Single-cell datasets can contain millions of cells with thousands of genes.
This skill covers strategies from simple to advanced for accelerating analysis.

## 1. Quick Setup (Always Use This)

Add to the **beginning of every notebook**:

```python
import os

# Detect available cores, reserve 2 for system stability
n_cores = os.cpu_count()
n_jobs = max(1, n_cores - 2)

# MUST set these BEFORE importing numpy/scanpy/pynndescent
os.environ["NUMBA_THREADING_LAYER"] = "workqueue"  # Force workqueue backend for pynndescent
os.environ["NUMBA_NUM_THREADS"] = str(n_jobs)  # Critical for neighbors/UMAP
os.environ["OMP_NUM_THREADS"] = str(n_jobs)
os.environ["OPENBLAS_NUM_THREADS"] = str(n_jobs)
os.environ["MKL_NUM_THREADS"] = str(n_jobs)
os.environ["NUMEXPR_MAX_THREADS"] = str(n_jobs)

import scanpy as sc
sc.settings.n_jobs = n_jobs
print(f"🚀 Using {n_jobs} cores for parallel computation")
```

## 2. Library-Specific Parallel Parameters

### Scanpy Functions

```python
# Neighborhood graph - parallelism via NUMBA_NUM_THREADS (pynndescent backend)
# NOTE: n_jobs parameter was REMOVED in newer Scanpy versions
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=50)  # Uses NUMBA_NUM_THREADS automatically

# Differential expression - still supports explicit n_jobs
sc.tl.rank_genes_groups(adata, groupby='cell_type', n_jobs=n_jobs)
```

### UMAP/t-SNE

```python
# UMAP uses numba internally, controlled by environment variables
sc.tl.umap(adata)  # Will use n_jobs from sc.settings

# For t-SNE, consider openTSNE for better parallelization
from openTSNE import TSNE
tsne = TSNE(n_jobs=n_jobs, random_state=42)
adata.obsm['X_tsne'] = tsne.fit(adata.obsm['X_pca'][:, :30])
```

## 3. Accelerating Pandas Operations

### Pandarallel (Drop-in Replacement)

For heavy `.apply()` operations on `adata.obs`:

```python
from pandarallel import pandarallel
pandarallel.initialize(progress_bar=True, nb_workers=n_jobs)

# Replace .apply() with .parallel_apply()
adata.obs['score'] = adata.obs['gene_list'].parallel_apply(
    lambda x: complex_calculation(x)
)
```

### Polars (10-100x Faster than Pandas)

For very large metadata operations:

```python
import polars as pl

# Convert pandas to polars
df_polars = pl.from_pandas(adata.obs)

# Polars auto-parallelizes, uses all cores by default
result = df_polars.with_columns([
    pl.col('column').map_elements(complex_function).alias('new_column')
])

# Convert back if needed
adata.obs = result.to_pandas()
```

## 4. Custom Loops with Joblib

For parallelizing your own analysis code:

```python
from joblib import Parallel, delayed

def analyze_sample(sample_id, adata):
    """Process one sample - this runs in parallel"""
    subset = adata[adata.obs['sample'] == sample_id].copy()
    # ... heavy computation ...
    return result

sample_list = adata.obs['sample'].unique()

# n_jobs=-1 uses all cores
results = Parallel(n_jobs=-1, backend='loky')(
    delayed(analyze_sample)(s, adata) for s in sample_list
)
```

## 5. GPU Acceleration with RAPIDS (Recommended for Large Data)

### When to Use GPU Acceleration

**✅ Use GPU when:**
- Dataset has \u003e50,000 cells
- Running iterative analyses (multiple clustering resolutions, parameter sweeps)
- Time-sensitive projects requiring rapid turnaround
- Performing computationally intensive steps: UMAP, neighbors, Leiden clustering

**⚠️ GPU may not help for:**
- Data loading, QC filtering, basic statistics
- Visualization (matplotlib/seaborn)
- Small datasets (\u003c10,000 cells)
- Steps that are already fast on CPU

### Check GPU Availability

```python
import subprocess

def check_gpu():
    try:
        result = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ GPU available")
            print(result.stdout.split('\\n')[0])  # Show GPU model
            return True
    except FileNotFoundError:
        pass
    print("❌ No GPU detected - using CPU parallelization")
    return False

HAS_GPU = check_gpu()
```

### Using rapids-singlecell

If GPU is available, `rapids-singlecell` provides 10-100x speedup for key operations:

```python
import rapids_singlecell as rsc

# Transfer data to GPU (automatic memory management)
rsc.get.anndata_to_GPU(adata)

# GPU-accelerated operations - identical API to scanpy
rsc.pp.normalize_total(adata, target_sum=1e4)
rsc.pp.log1p(adata)
rsc.pp.highly_variable_genes(adata, n_top_genes=3000, batch_key='sample')
rsc.pp.pca(adata, n_comps=50)
rsc.pp.neighbors(adata, n_neighbors=30, n_pcs=50)
rsc.tl.umap(adata, min_dist=0.3)  # 20-100x faster!
rsc.tl.leiden(adata, resolution=1.0)

# Trajectory analysis (if needed)
rsc.tl.diffmap(adata, n_comps=15)  # Diffusion map
rsc.tl.dpt(adata)  # Diffusion pseudotime

# Differential expression
rsc.tl.rank_genes_groups(adata, groupby='cell_type', method='wilcoxon')

# Transfer back to CPU for visualization/saving
rsc.get.anndata_to_CPU(adata)
```

> **Note**: For Harmony batch correction, use `harmonypy` on CPU after PCA - it's already well-optimized and GPU version offers minimal benefit.
```

### Performance Comparison

| Operation | CPU (60k cells) | GPU (60k cells) | Speedup |
|-----------|-----------------|-----------------|---------|
| HVG selection | ~20s | ~5s | 4x |
| PCA | ~30s | ~3s | 10x |
| Neighbors | ~3min | ~10s | 18x |
| UMAP | ~15min | ~20s | 45x |
| Leiden | ~1min | ~5s | 12x |
| **Total pipeline** | ~20min | ~1min | **20x** |

> **Note**: Actual speedup depends on GPU model (A100 \u003e V100 \u003e T4) and data characteristics.

## 6. Memory Optimization

> [!WARNING]
> A 60K×26K AnnData uses ~4-6GB. Multiple `.copy()` calls will multiply this!

### ✅ High-Performance Patterns (Use These!)

#### 1. Single-Pass Filtering (Prevent Sequential Copies)

**❌ BAD (Sequential Copies):**
```python
# Creates 5 full copies sequentially!
adata = adata[adata.obs['n_genes'] > 200].copy()
adata = adata[adata.obs['total_counts'] > 500].copy()
adata = adata[adata.obs['mt'] < 10].copy()
```

**✅ GOOD (Combined Mask):**
```python
# Combine masks first, then Apply ONCE
mask = (adata.obs['n_genes'] > 200) & \
       (adata.obs['total_counts'] > 500) & \
       (adata.obs['mt'] < 10)

print(f"Filtering {adata.n_obs} -> {mask.sum()} cells")
adata = adata[mask].copy()  # Only 1 memory allocation
```

#### 2. Composite Key Analysis (Avoid Subsetting)

**❌ BAD (Loop & Copy):**
```python
for ct in cell_types:
    # High memory pressure due to repeated copying
    sub = adata[adata.obs['cell_type'] == ct].copy() 
    sc.tl.rank_genes_groups(sub, groupby='condition', ...)
```

**✅ GOOD (In-Place w/ Composite Key):**
```python
# Create composite key, analyze in-place (Zero copy)
adata.obs['group'] = adata.obs['cell_type'].astype(str) + '_' + adata.obs['condition'].astype(str)
sc.tl.rank_genes_groups(adata, groupby='group', ...)
```

### Basic Efficiency Habits

```python
# Keep sparse
if not sp.issparse(adata.X): adata.X = sp.csr_matrix(adata.X)

# Avoid copy when possible (Use Views)
adata_view = adata[mask]  # ✅ View (no copy)

# If copy is unavoidable, delete original immediately
adata_new = adata[mask].copy(); del adata; gc.collect()

# Stage checkpoint pattern
adata.write_h5ad('checkpoint.h5ad'); del adata; gc.collect()
adata = sc.read_h5ad('checkpoint.h5ad')  # Reload when needed
```

### Common Pitfalls

| Pattern | Impact | Fix |
|---------|--------|-----|
| `adata.copy()` | 2× | Use views; delete original after copy |
| Multiple h5ad loaded | N× | Load→process→save→delete, one at a time |
| Scrublet/scVI | +4-8GB | `del scrub/model; gc.collect()` after use |

### Tool Tips

- **Scrublet**: `del scrub, X` immediately after `scrub_doublets()`
- **scVI**: Use `batch_size=128` (default 256) if OOM; checkpoint before training
- **Emergency**: `gc.collect()` or `manage_kernel(action="restart")`

## 7. Dask for Out-of-Core Computing

For datasets larger than RAM:

```python
import dask.array as da

# Convert to dask array with chunks
X_dask = da.from_array(adata.X, chunks=(10000, adata.n_vars))

# Operations are lazy - only compute when needed
result = X_dask.mean(axis=0).compute()
```

## Quick Reference Table

| Scenario | Solution | Complexity |
|----------|----------|------------|
| General Scanpy workflow | Set `sc.settings.n_jobs` + env vars | ⭐ |
| Slow Pandas `.apply()` | Use `pandarallel` or `polars` | ⭐⭐ |
| Custom for-loops | Use `joblib.Parallel` | ⭐⭐ |
| Have GPU available | Use `rapids-singlecell` | ⭐⭐⭐ |
| Data larger than RAM | Use `dask` | ⭐⭐⭐⭐ |
| Keep memory low | Keep `adata.X` sparse, avoid `.copy()` | ⭐ |
| Prevent OOM during scVI/Scrublet | Stage-based cleanup + checkpoint | ⭐⭐ |
| Monitor memory usage | Use `mem_check()` helper function | ⭐ |


## Installation

```bash
# Basic parallelization
pip install pandarallel polars joblib

# GPU acceleration (requires CUDA)
pip install rapids-singlecell cuml

# Out-of-core computing
pip install dask distributed
```

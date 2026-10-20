---
id: quality_control
name: Single-Cell Quality Control
description: |
  Standard quality control workflow for single-cell RNA-seq data.
  Includes filtering, normalization, and QC metric visualization.
tags: [qc, preprocessing, scanpy]
---

# Single-Cell Quality Control Workflow

Standard workflow for quality control of single-cell RNA-seq data using Scanpy.

---

## ⚠️ MANDATORY QC CHECKLIST ⚠️

> [!CAUTION]
> **YOU MUST COMPLETE ALL STEPS BELOW IN ORDER BEFORE PROCEEDING TO CLUSTERING**
> 
> The order is critical. Changing the order will invalidate your analysis.
> Follow the **Data Cleaning First** principle: clean raw data before any filtering or normalization.

### Phase 1: Data Cleaning (Raw Data Level)

These steps MUST be performed on RAW data BEFORE any filtering or normalization.

- [ ] **Step 1**: 🧪 **Ambient RNA Assessment** (REQUIRED if raw matrix exists)
      - Input: Raw counts including empty droplets
      - Tool: SoupX (if raw matrix available) or DecontX (fallback)
      - ⚠️ Must be done FIRST before any other QC steps
      - **Decision rule**: 
        - If `raw_feature_bc_matrix/` exists → MUST run SoupX
        - If only `filtered_feature_bc_matrix/` exists → Run DecontX OR document skip reason
      - **You MUST document your decision** (see Decision Documentation below)
      
- [ ] **Step 2**: Calculate QC metrics (MT%, counts, genes)
      - Based on: Corrected counts from Step 1 (or raw counts if Step 1 skipped)
      
- [ ] **Step 3**: � **Doublet Prediction** (RECOMMENDED)
      - Input: Corrected counts (before any gene-count filtering)
      - Tool: Scrublet or DoubletFinder
      - ⚠️ Must be done BEFORE filtering to preserve high-count reference cells
      - **Note**: Only calculate scores here; filtering happens in Step 5

### Phase 2: Cell Quality Filtering

- [ ] **Step 4**: Visualize QC metrics and Doublet scores
      - Include: Violin/scatter plots of counts, genes, MT%, **doublet scores**
      - Purpose: Data-driven threshold determination
      - ⚠️ Doublet scores MUST be visible for threshold selection
      
- [ ] **Step 5**: Apply filters (UNIFIED FILTERING)
      - Remove: High MT%, low genes, low counts, **AND predicted doublets**
      - All filtering happens HERE, not earlier
      - Document: Cell counts before/after, doublets removed

### Phase 3: Normalization & Feature Selection

- [ ] **Step 6**: Normalization
      - Preserve raw counts in layer first
      - Normalize → Log1p
      
- [ ] **Step 7**: Identify highly variable genes
      - Based on: Normalized, clean data

> [!WARNING]
> **CRITICAL ORDER CONSTRAINTS**:
> - Step 1 (Ambient RNA) MUST be done FIRST on raw data (if applicable)
> - Step 3 (Doublet) MUST calculate scores BEFORE filtering; **DO NOT filter in Step 3**
> - Step 5 is the ONLY place where cells are filtered (unified filtering)
> - Step 6 (Normalization) MUST be done AFTER all data cleaning (Steps 1-5)
> 
> Skipping or reordering these steps will produce scientifically invalid results.

---

## Prerequisites

```python
import scanpy as sc
import numpy as np
import matplotlib.pyplot as plt

sc.settings.verbosity = 3

# Initialize variables for QC summary (prevents NameError if steps fail)
contamination_rate = 0.0
n_predicted_doublets = 0
n_cells_before = 0
n_genes_before = 0
```


## Step 1: 🧪 Ambient RNA Assessment (REQUIRED if raw matrix exists)

> [!CAUTION]
> **YOU MUST CHECK FOR RAW MATRIX AND DOCUMENT YOUR DECISION**
> 
> This step is **conditionally required**:
> - If `raw_feature_bc_matrix/` exists → **MUST run SoupX**
> - If only `filtered_feature_bc_matrix/` exists → Attempt DecontX OR document skip reason
> - This step MUST be done FIRST, before any other QC metrics calculation
> 
> **Silently skipping this step is not allowed.** Add a markdown cell documenting your decision per the Decision Documentation Principle.

---

### Background
When cells break during sample preparation, their RNA is released into the droplet suspension.
This "ambient RNA" can be captured by other droplets, causing:
- False co-expression of unrelated markers
- Reduced marker specificity
- Incorrect cell type identification

### Workflow Selection
1.  **SoupX** (Primary): Use if you have CellRanger output with both `raw_feature_bc_matrix/` and `filtered_feature_bc_matrix/`.
2.  **DecontX** (Fallback): Use if only the filtered matrix is available.

---

### Option 1: SoupX (Primary Method)

**Step 1: Setup R interface** (Required for both SoupX and DecontX)
```python
# Setup rpy2 converters for AnnData <-> SingleCellExperiment conversion
import rpy2.robjects as robjects
from rpy2.robjects import pandas2ri
import anndata2ri

# Modern replacement for .activate()
robjects.default_converter += pandas2ri.converter + anndata2ri.converter
```

**Step 2: Verify data availability**
```python
import os
cellranger_dir = "path/to/cellranger_output"  # Contains raw_feature_bc_matrix/ and filtered_feature_bc_matrix/
use_soupx = os.path.isdir(os.path.join(cellranger_dir, "raw_feature_bc_matrix"))
print(f"SoupX available: {use_soupx}")
```

**Step 3: Run SoupX in R** (Execute in separate cell with `%%R` magic)
```r
%%R -i cellranger_dir -o soupx_out_dir -o contamination_rate

library(Seurat)
library(SoupX)
library(Matrix)
library(dplyr)

# Initialize outputs
soupx_out_dir <- "soupx_result"
contamination_rate <- 0.0

# === 1. Robust Data Loading & Cluster Check ===
tryCatch({
    # A. 🚨 CRITICAL: Try loading data (Handle both Folders and H5)
    sc <- tryCatch({ load10X(cellranger_dir) }, error=function(e) {
        # Fallback: Find H5 files automatically
        raw_h5 <- list.files(cellranger_dir, pattern="raw.*h5", full.names=T, recursive=T)[1]
        fil_h5 <- list.files(cellranger_dir, pattern="filtered.*h5", full.names=T, recursive=T)[1]
        if(is.na(raw_h5)) stop("No Raw H5 found for manual load")
        SoupChannel(Seurat::Read10X_h5(raw_h5), Seurat::Read10X_h5(fil_h5))
    })

    # B. 🚨 CRITICAL: Ensure Clusters Exist 🚨
    # autoEstCont() fails without clusters. Run quick Seurat if missing.
    if (is.null(sc$metaData$clusters)) {
        cat("🚀 Generating quick clusters for SoupX...\n")
        srat <- CreateSeuratObject(sc$toc) %>% 
            NormalizeData(verbose=F) %>% FindVariableFeatures(verbose=F) %>% 
            ScaleData(verbose=F) %>% RunPCA(verbose=F) %>% 
            FindNeighbors(verbose=F) %>% FindClusters(verbose=F)
        sc <- setClusters(sc, setNames(as.character(srat@meta.data$seurat_clusters), colnames(srat)))
    }

    # === 2. Estimate & Save ===
    sc <- autoEstCont(sc)
    contamination_rate <- sc$fit$rhoEst
    
    # Only correct if rate is reasonable (>1% and <50%)
    if (contamination_rate > 0.01 && contamination_rate < 0.5) {
        cat(sprintf("✓ SoupX Contamination: %.2f%%. Saving to disk...\n", contamination_rate*100))
        out <- adjustCounts(sc)
        
        # Save to disk (Avoids memory crash)
        dir.create(soupx_out_dir, showWarnings=F)
        if (requireNamespace("DropletUtils", quietly=T)) {
            DropletUtils::write10xCounts(soupx_out_dir, out, version="3")
        } else {
            # Manual fallback to MTX
            Matrix::writeMM(out, file.path(soupx_out_dir, "matrix.mtx"))
            write.table(rownames(out), file.path(soupx_out_dir, "genes.tsv"), sep="\t", quote=F, row.names=F, col.names=F)
            write.table(colnames(out), file.path(soupx_out_dir, "barcodes.tsv"), sep="\t", quote=F, row.names=F, col.names=F)
        }
    } else {
        soupx_out_dir <- "NULL" # Signal python: No correction needed
        cat("✓ No correction applied (Rate too low/high).\n")
    }

}, error = function(e) {
    cat(paste("Error in SoupX:", e$message, "\n"))
    soupx_out_dir <<- "NULL" 
})
```

**Step 4: Apply corrected counts to AnnData**
```python
if soupx_out_dir != "NULL" and os.path.exists(soupx_out_dir):
    print(f"✓ Loading corrected counts from: {soupx_out_dir}")
    try:
        # Load from disk
        adata_corr = sc.read_10x_mtx(soupx_out_dir, var_names='gene_symbols', cache=False)
    except:
        # Fallback for manual file names
        adata_corr = sc.read_10x_mtx(soupx_out_dir, var_names='gene_symbols', make_unique=True)

    # Update adata.X with clean data
    adata.layers['soupX_counts'] = adata_corr.X.copy()
    adata.X = adata.layers['soupX_counts'].copy()
    adata.uns['contamination_rate'] = float(contamination_rate)
    
    # Cleanup temp folder (Optional)
    # shutil.rmtree(soupx_out_dir)
else:
    print("✓ No correction applied (or SoupX failed). Using original counts.")
```

---

### Option 2: DecontX (Fallback Method)
Use when raw matrix is unavailable. **Requires Step 1 setup above.**

**Run DecontX in R:**
```r
%%R -i cellranger_dir -o decontx_out_dir -o decontx_rho

library(celda)
library(SingleCellExperiment)

# Data loading (filtered matrix only), error handling, and result saving logic are omitted here; implement them following the exact pattern from Step 3 (SoupX) above.
# core logic
sce <- decontX(adata)
decontx_counts <- decontXcounts(sce)
decontx_contamination <- colData(sce)$decontX_contamination
# save data to disk ...

cat("Mean contamination:", round(mean(decontx_contamination) * 100, 1), "%\n")
```

---

### Detection Signs (verify during downstream analysis)
- Cell-type-specific genes appearing in ALL clusters (e.g., Hb genes everywhere)
- Marker genes lacking specificity in dotplot/heatmap

> [!WARNING]
> If you detect contamination signs AFTER clustering:
> 1. **STOP** and go back to QC stage
> 2. Apply SoupX/DecontX correction
> 3. Re-run the entire analysis from normalization

> [!TIP]
> **Advanced Option**: For datasets with severe contamination (>20%) or when maximum accuracy is critical, consider [CellBender](https://github.com/broadinstitute/CellBender) `remove-background`.
> - **Pros**: Highest accuracy, near-optimal denoising
> - **Cons**: Requires GPU (CUDA), ~30-60min runtime per sample
> - **Use when**: GPU available AND (contamination >20% OR critical marker detection needed)


## Step 2: Calculate QC Metrics

```python
# Calculate mitochondrial gene percentage
adata.var['mt'] = adata.var_names.str.startswith('MT-')  # Human
# adata.var['mt'] = adata.var_names.str.startswith('mt-')  # Mouse

# Calculate ribosomal gene percentage (optional)
adata.var['ribo'] = adata.var_names.str.startswith(('RPS', 'RPL'))  # Human

# Calculate hemoglobin genes (optional, for blood samples)
adata.var['hb'] = adata.var_names.str.match('^HB[^P]')  # Human

# Compute QC metrics
sc.pp.calculate_qc_metrics(
    adata, 
    qc_vars=['mt', 'ribo', 'hb'],
    percent_top=None, 
    log1p=False, 
    inplace=True
)
```

## Step 3: Doublet Prediction (RECOMMENDED - Score Only, Before Filtering)

> [!CAUTION]
> **This step MUST be done BEFORE any gene-count filtering (Step 5)**
> 
> Doublet detection helps identify droplets containing multiple cells.
> Scrublet needs high-count cells as reference for simulation.
> If you filter first, you lose these reference cells and detection accuracy drops significantly.
>
> ⚠️ **IMPORTANT**: Only calculate and record scores here. **DO NOT filter cells in this step.**
> All filtering happens in Step 5 after visualization.

### Common Approaches
- Simulation-based methods (e.g., scrublet, DoubletFinder)
- Clustering-based detection
- Hybrid approaches

### Cell Count Check
```python
# Scrublet requires sufficient cells for reliable simulation
n_cells = adata.n_obs
print(f"Cell count: {n_cells}")
if n_cells < 500:
    print("⚠️ Warning: Low cell count may reduce doublet detection accuracy")
    print("   Consider skipping doublet detection for very small datasets")
```

### Calculate Doublet Scores (DO NOT FILTER)
```python
# Using scrublet for doublet detection
import scrublet as scr

# Run on raw counts
scrub = scr.Scrublet(adata.layers['counts'] if 'counts' in adata.layers else adata.X)
doublet_scores, predicted_doublets = scrub.scrub_doublets()

# Save scores to metadata - DO NOT FILTER HERE
adata.obs['doublet_score'] = doublet_scores
adata.obs['predicted_doublet'] = predicted_doublets

# Report statistics (filtering happens in Step 5)
print(f"Predicted doublets (to be filtered in Step 5): {sum(predicted_doublets)}")
print(f"Doublet rate: {sum(predicted_doublets) / len(predicted_doublets) * 100:.1f}%")
# DO NOT filter here - all filtering is unified in Step 5
```




## Step 4: Visualize QC Metrics (Including Doublet Scores)

> [!IMPORTANT]
> Doublet scores MUST be visualized here to determine appropriate thresholds.
> Since doublets have NOT been filtered yet, you can see the full score distribution.

```python
# Violin plots of key metrics (including doublet score)
fig, axes = plt.subplots(1, 4, figsize=(16, 4))

sc.pl.violin(adata, 'n_genes_by_counts', ax=axes[0], show=False)
axes[0].set_title('Genes per Cell')

sc.pl.violin(adata, 'total_counts', ax=axes[1], show=False)
axes[1].set_title('UMI Counts per Cell')

sc.pl.violin(adata, 'pct_counts_mt', ax=axes[2], show=False)
axes[2].set_title('% Mitochondrial')

# Doublet score visualization - CRITICAL for threshold selection
if 'doublet_score' in adata.obs.columns:
    sc.pl.violin(adata, 'doublet_score', ax=axes[3], show=False)
    axes[3].set_title('Doublet Score')
    axes[3].axhline(y=0.25, color='r', linestyle='--', alpha=0.5, label='Default threshold')
else:
    axes[3].text(0.5, 0.5, 'Doublet detection\nnot performed', ha='center', va='center')
    axes[3].set_title('Doublet Score (N/A)')

plt.tight_layout()
plt.savefig('qc_metrics_violin.png', dpi=150, bbox_inches='tight')
plt.savefig('qc_metrics_violin.pdf', bbox_inches='tight')
plt.show()
```

```python
# Scatter plots for threshold selection
fig, axes = plt.subplots(1, 2, figsize=(10, 4))

sc.pl.scatter(adata, x='total_counts', y='n_genes_by_counts', 
              color='pct_counts_mt', ax=axes[0], show=False)
axes[0].set_xlabel('Total Counts')
axes[0].set_ylabel('Genes Detected')

sc.pl.scatter(adata, x='total_counts', y='pct_counts_mt',
              ax=axes[1], show=False)
axes[1].set_xlabel('Total Counts')
axes[1].set_ylabel('% Mitochondrial')

plt.tight_layout()
plt.savefig('qc_scatter.png', dpi=150, bbox_inches='tight')
plt.show()
```

## Step 4b: Determine Filtering Thresholds

> [!IMPORTANT]
> Thresholds should be determined by examining the data distributions.
> The values below are typical starting points; adjust based on your data.

```python
# Print summary statistics
print("Summary statistics:")
print(f"  n_genes_by_counts: {adata.obs['n_genes_by_counts'].describe()}")
print(f"  total_counts: {adata.obs['total_counts'].describe()}")
print(f"  pct_counts_mt: {adata.obs['pct_counts_mt'].describe()}")
if 'doublet_score' in adata.obs.columns:
    print(f"  doublet_score: {adata.obs['doublet_score'].describe()}")

# Common thresholds (adjust based on plots)
min_genes = 200       # Minimum genes per cell
max_genes = 5000      # Maximum genes per cell (doublet filter)
min_counts = 500      # Minimum UMI counts
max_mt_pct = 20       # Maximum mitochondrial percentage
max_doublet_score = 0.25  # Doublet score threshold (if doublet detection was performed)
```

## Step 5: Apply Filters (UNIFIED FILTERING)

> [!IMPORTANT]
> **This is the ONLY step where cells are filtered.**
> All filtering criteria (QC metrics + doublets) are applied together here.

```python
# Unified filtering - all criteria applied together
print(f"Cells before filtering: {adata.n_obs}")

# Create QC masks
mask_min_genes = adata.obs['n_genes_by_counts'] >= min_genes
mask_max_genes = adata.obs['n_genes_by_counts'] < max_genes
mask_min_counts = adata.obs['total_counts'] >= min_counts
mask_max_mt = adata.obs['pct_counts_mt'] < max_mt_pct

# Create doublet mask (keep cells that are NOT predicted doublets)
if 'predicted_doublet' in adata.obs.columns:
    mask_doublet = ~adata.obs['predicted_doublet']
    n_doublets = (~mask_doublet).sum()
else:
    mask_doublet = True  # No doublet filtering if not calculated
    n_doublets = 0

# Combine ALL masks and apply ONCE
final_mask = mask_min_genes & mask_max_genes & mask_min_counts & mask_max_mt & mask_doublet
adata = adata[final_mask].copy()

print(f"Cells after filtering: {adata.n_obs}")
print(f"  - Removed as doublets: {n_doublets}")
print(f"  - Removed by QC metrics: {(~(mask_min_genes & mask_max_genes & mask_min_counts & mask_max_mt)).sum()}")

# Filter genes (keep genes expressed in at least N cells)
sc.pp.filter_genes(adata, min_cells=3)
print(f"Genes after filtering: {adata.n_vars}")
```

## Step 6: Normalization

```python
# Store raw counts SAFELY before normalization
# IMPORTANT: Use .copy() to prevent reference issues with sparse matrices
adata.layers['counts'] = adata.X.copy()  # FORCE COPY to prevent reference modification

# Normalize to 10,000 counts per cell
sc.pp.normalize_total(adata, target_sum=1e4)

# Log transform
sc.pp.log1p(adata)

# Store normalized data
adata.raw = adata
```

## Step 7: Identify Highly Variable Genes

```python
# Find highly variable genes
sc.pp.highly_variable_genes(
    adata,
    min_mean=0.0125,
    max_mean=3,
    min_disp=0.5,
    n_top_genes=2000,  # Alternative: specify number
    batch_key=None,    # Set if batch correction needed
)

# Visualize
sc.pl.highly_variable_genes(adata, show=False)
plt.savefig('hvg_selection.png', dpi=150, bbox_inches='tight')
plt.show()

print(f"Highly variable genes: {sum(adata.var['highly_variable'])}")
```

## Post-QC Summary

```python
# Generate QC report
# Handle case where doublet detection was not performed
doublet_info = ""
if 'predicted_doublet' in adata.obs.columns:
    # Note: After filtering, predicted_doublet should be all False
    # Use stored variable from Step 5 for accurate count
    doublet_info = f"""### Step 3: Doublet Detection
- Method: Scrublet
- Doublets removed in Step 5: {n_doublets if 'n_doublets' in dir() else 'N/A'}
"""
else:
    doublet_info = """### Step 3: Doublet Detection
- Status: Skipped (low cell count or not applicable)
"""

qc_summary = f"""
## Quality Control Summary

### Step 1: Ambient RNA Assessment
- Estimated contamination rate: {contamination_rate:.1%}
- Correction applied: {'Yes' if contamination_rate > 0.10 else 'No (< 10% or skipped)'}
- Method: SoupX / DecontX

### Step 2: Initial QC Metrics
- Cells before QC: {n_cells_before}
- Genes before QC: {n_genes_before}

{doublet_info}
### Step 5: Unified Filtering
- Min genes per cell: {min_genes}
- Max genes per cell: {max_genes}
- Min UMI counts: {min_counts}
- Max % mitochondrial: {max_mt_pct}%
- Cells after filtering: {adata.n_obs}
- Genes after filtering: {adata.n_vars}

### Steps 6-7: Normalization & Feature Selection
- Normalized to 10,000 counts per cell
- Log1p transformed
- Highly variable genes: {sum(adata.var['highly_variable'])}

### Steps Verification
- [{'x' if contamination_rate > 0 else ' '}] Step 1: Ambient RNA assessment - {'COMPLETED' if contamination_rate > 0 else 'SKIPPED'}
- [{'x' if 'predicted_doublet' in adata.obs.columns else ' '}] Step 3: Doublet detection - {'COMPLETED' if 'predicted_doublet' in adata.obs.columns else 'SKIPPED'}
- [x] Step 5: Unified filtering - COMPLETED
"""
print(qc_summary)
```




## Quality Gate

Before proceeding to downstream analysis, summarize the quality status:

**Checklist** (document in your QC report):
- [ ] Doublet detection: method used (or reason for skipping), doublets removed in Step 5
- [ ] Ambient RNA: contamination level assessed (if raw matrix available), correction applied if > 10%
- [ ] Filtering thresholds: values used and justification (all applied in Step 5)
- [ ] Cell counts: per sample before/after unified filtering

**Flag for leader review if**:
- Marker genes appear in >50% of cells unexpectedly (potential ambient contamination)
- Any sample has <500 cells after filtering
- Doublet rate > 15% (may indicate loading issues)
- Major quality concerns that may affect interpretation

## Tips

> [!TIP]
> - Always visualize distributions before setting thresholds
> - Different tissue types may require different thresholds
> - For FFPE or low-quality samples, use more lenient thresholds
> - Consider batch effects when determining thresholds

> [!WARNING]
> Overly aggressive filtering can remove rare cell populations.
> Underly lenient filtering can introduce noise and doublets.


# Single-cell RNA-seq Analysis

## Overview

Single-cell RNA-seq analysis pipeline with omicverse integration. This skill provides comprehensive workflow guidance for scRNA-seq data processing including quality control, preprocessing, dimensionality reduction, clustering, and cell type annotation using LLM-powered analysis.

## Main Workflow

### Analysis Pipeline

**Parameters:** `data_path` - Path to the input data (h5ad, h5, mtx, etc.)

**Critical Python Environment Rules:**
- **PERSISTENT STATE:** Python interpreter maintains ALL variables across calls!
- **MEMORY OPTIMIZATION:** Variables persist! NEVER re-read or re-import data that already exists in memory!
- **SMART VARIABLE CHECKING:** Use `try/except` or `'var' in globals()` to check existence - NO redundant file I/O!
- **EFFICIENCY FIRST:**
  - Check if adata exists before loading: `if 'adata' not in globals()`
  - Use existing results: `if 'pca_result' in adata.obsm`
  - Reuse computed values: `if 'marker_genes' in locals()`
- **ERROR RECOVERY:** If code fails, analyze error and fix - don't reload everything!
- **NO REPETITION:** Each import/load/compute happens ONCE per session unless explicitly needed
- **After each step:** `mark_task_done("description")`, then `show_todos()`
- **AUTOMATIC EXECUTION:** Proceed automatically without confirmations; log warnings when needed

### Phase 0 - Setup & Validation

1. Data discovery: Use `ls` command to check folder contents, then proceed with file loading
2. Environment check will be done automatically within data loading step

### Phase 1 - TODO Creation (Once Only)

Execute: `current = show_todos()`

IF current is EMPTY, create these todos ONCE:
1. "Check Python environment and load initial data"
2. "Inspect data structure and determine processing pipeline"
3. "Apply quality control with omicverse.pp.qc"
4. "Perform preprocessing with omicverse.pp.preprocess"
5. "Compute PCA with omicverse.pp.pca"
6. "Apply batch correction if needed"
7. "Run clustering analysis"
8. "Ask user for data context (tissue/condition)"
9. "Generate context-specific cell types and markers from description"
10. "Find cluster-specific marker genes from data"
11. "Calculate AUCell scores for cell type markers"
12. "Annotate cell type with LLM"
13. "Conduct downstream analysis"
14. "Generate analysis report"

**Automatic Workflow Mode:**
- Execute each todo task automatically without asking for confirmation
- After successful completion of any step, immediately call `mark_task_done("description")` and proceed to next
- Continue the workflow seamlessly until all tasks complete or user intervenes

### Phase 2 - Adaptive Execution Workflow

**Critical Execution Strategy:**

When you call `scrna.run_scrna_workflow()`, it returns guidance, explanations, and example Python code. You MUST:

1. **Read and analyze** the entire returned content carefully
2. **Understand the logic** and methodology described
3. **Adapt the provided code** to your current data situation (adata shape, available columns, etc.)
4. **Modify parameters** based on your actual data characteristics
5. **Execute the adapted code** - NOT the original code directly
6. **Handle errors** by adjusting code based on the guidance provided

**Result Analysis Requirement:**

After executing any code:
1. **Analyze the output** - Don't just print and move on!
2. **Interpret the results** - What do the numbers, plots, and warnings mean?
3. **Check for issues** - Are there data quality problems or unexpected patterns?
4. **Make decisions** - Should parameters be adjusted based on what you observed?
5. **Document findings** - Save key insights to results directory
6. **Proceed intelligently** - Use results to inform next steps

---

## Step-by-Step Guide

### Step 1 - Data Loading, Inspection & Project Setup

```python
# EFFICIENT DATA LOADING - Check memory first!
try:
    # Check if adata exists and is valid
    print(f"Using existing adata: {adata.shape} (n_obs, n_var)")
    data_already_loaded = True
except NameError:
    # Only load if not in memory
    print("Loading data for the first time...")

    # Check and install required packages
    import subprocess
    import sys
    print("Checking required packages...")
    required = ['scanpy', 'omicverse', 'pandas', 'numpy']
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            print(f"Installing {pkg}...")
            subprocess.run([sys.executable, '-m', 'pip', 'install', pkg])

    import scanpy as sc
    import omicverse as ov
    import pandas as pd
    import numpy as np

    # Load data based on detected format
    adata = sc.read_xxx("path")  # .h5ad, .h5, .mtx, etc.
    print(f"Loaded: {adata.shape} (n_obs, n_var)")
    data_already_loaded = False

# Only import libraries once
if 'sc' not in globals():
    import scanpy as sc
    import omicverse as ov
    import pandas as pd
    import numpy as np
    print("Libraries imported")

# Create structured output directory (only if not exists)
print("\n📁 Setting up project structure...")
if 'results_dir' not in globals():
    import os
    from datetime import datetime

    # Create main results directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = f"scrna_analysis_results_{timestamp}"
    os.makedirs(results_dir, exist_ok=True)

# Create subdirectories
subdirs = [
    "01_data_loading",
    "02_quality_control",
    "03_preprocessing",
    "04_dimensionality_reduction",
    "05_batch_correction",
    "06_clustering",
    "07_cell_type_annotation",
    "08_visualization",
    "09_downstream_analysis",
    "10_reports",
    "logs"
]

for subdir in subdirs:
    os.makedirs(os.path.join(results_dir, subdir), exist_ok=True)

# Store results directory in adata
if 'adata' in globals():
    adata.uns['results_directory'] = results_dir

# Initial data inspection
print("\n🔍 Running initial data inspection...")
data_inspection = scrna.load_and_inspect_data(data_path="{data_path}", output_dir=results_dir)
```

### Step 2 - Quality Control

Get QC guidance and adapt the code to your data:

```python
scrna.run_scrna_workflow(workflow_type="qc")
```

Then analyze the returned guidance and implement adapted QC code based on your adata structure.

**CRITICAL:** Analyze QC results - cell counts, gene expression distributions, mitochondrial percentages. Interpret plots and decide on filtering thresholds.

```python
# Check if QC already done
if 'nUMIs' not in adata.obs.columns:
    print("\n📊 Running Quality Control...")

    import omicverse as ov
    # MANDATORY: Check help first before any omicverse function
    help(ov.pp.qc)

    # Apply QC with actual omicverse parameters
    qc_tresh = dict(mito_perc=0.2, nUMIs=500, detected_genes=250)
    ov.pp.qc(adata,
            mode='seurat',           # 'seurat' or 'mads'
            min_cells=3,
            min_genes=200,
            mt_startswith='MT-',     # Mitochondrial gene prefix
            tresh=qc_tresh)
    print("✅ QC completed successfully")
else:
    print("✅ QC already completed - skipping")
```

### Step 3 - Preprocessing

Get preprocessing guidance and adapt the code to your data:

```python
scrna.run_scrna_workflow(workflow_type="preprocessing")
```

```python
# Check if preprocessing needed
needs_preprocessing = (
    ('highly_variable' or 'highly_variable_features') not in adata.var.columns or
    'counts' not in adata.layers or
    adata.X.max() > 50  # Raw counts detected
)

if needs_preprocessing:
    import omicverse as ov
    # MANDATORY: Check help first
    help(ov.pp.preprocess)

    # Use actual omicverse preprocess parameters
    adata = ov.pp.preprocess(adata,
                            mode='shiftlog|pearson',    # normalization|HVG method
                            target_sum=50*1e4,          # Target sum for normalization
                            n_HVGs=2000,                # Number of HVGs
                            organism='human',           # 'human' or 'mouse'
                            no_cc=False)                # Remove cell cycle genes
    adata.var['highly_variable'] = adata.var['highly_variable_features']
    print("✅ Preprocessing completed successfully")
else:
    print("✅ Data already preprocessed - skipping")
```

### Step 4 - PCA

Get PCA guidance and adapt the code to your data:

```python
scrna.run_scrna_workflow(workflow_type="pca")
```

```python
# Check if scaling and PCA needed
needs_scaling = 'scaled' not in adata.layers
needs_pca = 'scaled|original|X_pca' not in adata.obsm.keys()

if needs_scaling:
    print("\n🔢 Scaling data...")
    import omicverse as ov
    help(ov.pp.scale)

    ov.pp.scale(adata,
               max_value=10,              # Clip values above this
               layers_add='scaled')       # Add to 'scaled' layer

if needs_pca:
    print("\n🔢 Computing PCA...")
    help(ov.pp.pca)

    ov.pp.pca(adata,
             n_pcs=50,                   # Number of principal components
             layer='scaled',             # Use scaled data
             inplace=True)               # Modify adata in place
    print("✅ PCA completed successfully")
```

### Step 5 - Batch Correction (if needed)

```python
# EFFICIENT BATCH CHECK - Only compute if not done before
if 'real_batch_keys' not in globals():
    # Check if batch correction is needed - look for REAL batch keys
    # Real batch keys are typically: 'batch', 'sample', 'donor', 'experiment', 'plate', 'condition'
    # NOT QC metrics like 'passing_mt', 'passing_ngenes', 'n_genes', 'total_counts', etc.

    real_batch_keys = []
    potential_batch_keys = ['batch', 'sample', 'donor', 'experiment', 'plate', 'condition', 'library_id']

    for key in potential_batch_keys:
        if key in adata.obs.columns:
            unique_vals = adata.obs[key].nunique()
            if unique_vals > 1 and unique_vals < adata.n_obs * 0.5:
                real_batch_keys.append(key)
                print(f"Found real batch key: {key} with {unique_vals} unique values")

if real_batch_keys:
    print(f"\n🔧 Real batch keys detected: {real_batch_keys}")
    scrna.run_scrna_workflow(workflow_type="batch_correction")
    # Then implement adapted batch correction code
else:
    print("\n✅ No real batch keys found - skipping batch correction")
```

### Step 6 - Clustering

Get clustering guidance and adapt the code to your data:

```python
scrna.run_scrna_workflow(workflow_type="clustering")
```

```python
# Determine which representation to use
if 'X_harmony' in adata.obsm.keys():
    use_rep = 'X_harmony'
elif 'X_scanorama' in adata.obsm.keys():
    use_rep = 'X_scanorama'
elif 'X_scVI' in adata.obsm.keys():
    use_rep = 'X_scVI'
else:
    use_rep = 'X_pca'

# Check if clustering needed
needs_neighbors = 'neighbors' not in adata.uns.keys()
needs_clustering = 'leiden' not in adata.obs.columns

if needs_neighbors:
    print("\n🎯 Computing neighborhood graph...")
    import scanpy as sc
    sc.pp.neighbors(adata, n_neighbors=15, use_rep=use_rep)

if needs_clustering:
    print("\n🎯 Running clustering...")
    import omicverse as ov
    help(ov.utils.cluster)

    ov.utils.cluster(adata,
                    method='leiden',
                    use_rep=use_rep,
                    random_state=1024,
                    resolution=0.5,
                    key_added='leiden')
    print("✅ Clustering completed successfully")
```

### Step 7 - UMAP Visualization

```python
scrna.run_scrna_workflow(workflow_type="umap")
```

```python
import scanpy as sc
if 'X_umap' not in adata.obsm.keys():
    print("\n🎯 Computing UMAP...")
    sc.tl.umap(adata, random_state=0)
    print("✅ UMAP computed successfully")
```

### Step 8 - Data Context Collection

```python
# EFFICIENT CONTEXT COLLECTION - Check if already collected
if 'user_data_context' not in globals():
    print("\n📝 **DATA CONTEXT COLLECTION**")
    user_data_context = input("Please briefly describe your data (tissue, condition, experiment): ").strip()
    print(f"Data context recorded: {user_data_context}")

    # Store context in adata
    adata.uns['user_data_context'] = user_data_context
else:
    print(f"Using existing data context: {user_data_context}")
```

### Step 9 - Marker Generation from Description

Get marker generation guidance based on data context:

```python
scrna.run_scrna_workflow(workflow_type="marker_from_desc", description=user_data_context)
```

**IMPORTANT:** Generate 20 cell types based on actual biological knowledge of the tissue/context. This replaces hardcoded examples with context-aware generation.

```python
# Example output format:
expected_cell_types = dict()
expected_cell_types['T Cells'] = ['CD3D', 'CD3E', ...]
expected_cell_types['B Cells'] = ['MS4A1', 'CD79A', ...]
# ... more cell types based on tissue context
```

### Step 10 - Marker Discovery from Data

Get data-driven marker analysis guidance:

```python
scrna.run_scrna_workflow(workflow_type="marker_from_data")
```

```python
import omicverse as ov
import scanpy as sc

cluster_markers = None
try:
    help(ov.single.get_celltype_marker)

    cluster_markers = ov.single.get_celltype_marker(adata,
                                                   clustertype='leiden',
                                                   log2fc_min=1,
                                                   pval_cutoff=0.05,
                                                   topgenenumber=10,
                                                   rank=True,
                                                   unique=False)
    print("✅ Cluster markers extracted with get_celltype_marker")
except:
    sc.tl.rank_genes_groups(adata, groupby='leiden', method='wilcoxon')
    cluster_markers = {}
    for cluster in adata.obs['leiden'].cat.categories:
        cluster_markers[cluster] = adata.uns['rank_genes_groups']['names'][cluster][:10].tolist()
    print("✅ Cluster markers extracted with scanpy")

print(cluster_markers)
adata.uns['cluster_markers'] = cluster_markers
```

### Step 11 - AUCell Cell Type Scoring

Get AUCell scoring guidance and methodology:

```python
scrna.run_scrna_workflow(workflow_type="aucell")
```

```python
import omicverse as ov
help(ov.single.geneset_aucell)

print("\n📊 Calculating AUCell scores for cell type markers...")

# Calculate AUCell scores for each expected cell type
for cell_type, markers in expected_cell_types.items():
    try:
        ov.single.geneset_aucell(adata,
                            geneset_name=cell_type,
                            geneset=markers,
                            AUC_threshold=0.01,
                            seed=42)
        print(f"✅ AUCell score calculated for {cell_type}")
    except Exception as e:
        print(f"❌ AUCell failed for {cell_type}: {e}")

print("\n📈 AUCell scores added to adata.obs")
```

### Step 12 - LLM-powered Cell Type Annotation

Get LLM-powered annotation guidance:

```python
scrna.run_scrna_workflow(workflow_type="llm_anno", description=user_data_context)
```

**Annotation Process:**

For each cluster, analyze:
1. Cluster markers from `adata.uns['cluster_markers']`
2. AUCell score analysis (cluster mean vs other clusters)
3. Top AUCell predictions ranked by fold enrichment

**Your task:** Identify the most likely biological cell type for each cluster:
- Interpret marker genes in context of known cell type-specific markers (PanglaoDB, CellMarker, literature)
- Cross-check with AUCell scores
- If evidence agrees, assign that cell type
- If they conflict, prioritize marker gene interpretation but explain discrepancy
- In ambiguous cases, provide top 3 likely cell types ranked by confidence

```python
# Output format:
llm_anno_results = {
    "0": "T Cells",
    "1": "B Cells",
    # ...
}

# Apply annotation
adata.obs['celltype_llm'] = adata.obs['leiden'].map(llm_anno_results)
```

### Step 13 - Downstream Analysis & Reporting

```python
print("\n🧬 Conducting downstream analysis...")

report_generation = scrna.generate_report(
    data_path="{data_path}",
    output_dir=results_dir,
    include_qc=True,
    include_clustering=True,
    include_annotation=True
)

print("✅ Downstream analysis and reporting complete")
```

---

## Available Toolset Functions

### Unified Workflow Engine

- `scrna.run_scrna_workflow(workflow_type="qc")` - Quality control with omicverse
- `scrna.run_scrna_workflow(workflow_type="preprocessing")` - Preprocessing with omicverse
- `scrna.run_scrna_workflow(workflow_type="pca")` - PCA with omicverse
- `scrna.run_scrna_workflow(workflow_type="clustering")` - Clustering analysis
- `scrna.run_scrna_workflow(workflow_type="umap")` - Calculate UMAP
- `scrna.run_scrna_workflow(workflow_type="aucell")` - AUCell scoring
- `scrna.run_scrna_workflow(workflow_type="batch_correction")` - Batch correction
- `scrna.run_scrna_workflow(workflow_type="marker_from_desc", description=...)` - Generate markers from description
- `scrna.run_scrna_workflow(workflow_type="marker_from_data")` - Find markers from data
- `scrna.run_scrna_workflow(workflow_type="llm_anno", description=...)` - LLM annotation

### Utility Functions

- `scrna.load_and_inspect_data(data_path, output_dir)` - Load and inspect data
- `scrna.generate_report(data_path, output_dir, ...)` - Generate analysis report

---

## Efficiency Principles

1. **CHECK BEFORE COMPUTE:** Always check if variables/results exist before recomputing
2. **USE TRY/EXCEPT:** Gracefully handle missing variables without re-reading files
3. **MEMORY-FIRST:** Trust the persistent Python interpreter - no redundant I/O
4. **SMART RECOVERY:** Fix errors in-place, don't restart entire analysis
5. **INCREMENTAL PROGRESS:** Each step builds on previous results

### Example Patterns

```python
# Good - Check memory first
try:
    print(f"Using existing adata: {adata.shape}")
except NameError:
    adata = sc.read_h5ad(path)

# Good - Check computed results
if 'X_pca' not in adata.obsm:
    sc.tl.pca(adata)
else:
    print("PCA already computed")

# Bad - Redundant file I/O
adata = sc.read_h5ad(path)  # Don't do this if adata exists!
```

**Remember:** Maintain persistent state, avoid redundant operations, and mark tasks complete with `mark_task_done()`!

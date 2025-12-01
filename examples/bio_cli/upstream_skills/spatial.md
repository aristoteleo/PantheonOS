# Spatial Transcriptomics Analysis

## Overview

Spatial transcriptomics analysis pipeline with omicverse integration for bin-to-cell analysis. This skill provides workflow guidance for processing Visium HD spatial data, including cell segmentation using Cellpose (H&E and GEX-based), label expansion, and bin-to-cell conversion.

## Main Workflow

### Bin2Cell Analysis Pipeline

**Available Workflow Tools:**

You have access to the `Spatial_Bin2Cell_Analysis` tool for getting official templates. Use the tool when you need guidance, official examples, or best practices for a specific workflow step.

You need to ask the user for the path of the data, then use `ls` command to check the folder contents, and proceed with file loading.

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
2. "Run read_visium workflow"
3. "Run cellpose_he workflow"
4. "Run expand_labels workflow"
5. "Run cellpose_gex workflow"
6. "Run salvage_secondary_labels workflow"
7. "Run bin2cell workflow"

**Automatic Workflow Mode:**
- Execute each todo task automatically without asking for confirmation
- After successful completion of any step, immediately call `mark_task_done("description")` and proceed to next
- Continue the workflow seamlessly until all tasks complete or user intervenes

### Phase 2 - Intelligent Execution Strategy

**Smart Decision Making:**

**Assess Current Situation First:**
- What data do you have loaded?
- What analysis steps are completed?
- What specific guidance do you need?

**Choose Your Approach:**

**Option A - Use Template Tool (Recommended for new users or complex steps):**
- Call `Spatial_Bin2Cell_Analysis(workflow_type="<type>")` to get official template
- Study the returned guidance and code patterns
- Adapt the template to your specific data
- Execute the adapted code

**Option B - Direct Implementation (For experienced users with clear requirements):**
- Directly write and execute code based on omicverse documentation
- Use `help()` functions to check parameters
- Follow established best practices

**When to Use Template Tool:**
- When you need official guidance for a workflow step
- When you want to see best practices and parameter examples
- When you're unsure about the correct approach
- When you want standardized, tested code patterns

**When Direct Implementation is OK:**
- When you have clear requirements and know the approach
- When adapting previous successful code
- When making minor parameter adjustments

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

**Data Path Requirements:**
- The path is the path of the data; use `ls` command to check folder contents
- If you don't know the path, ask the user and use `ls` to explore folder contents and structure
- Find something like `"binned_outputs/square_002um/"` in the path, then load the data
- The image should be stored somewhere in the path; find the correct image and ask user to confirm
- The image should end with `.btf` or `.tiff`

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
    adata = ov.space.read_visium_10x("path")
    print(f"Loaded adata: {adata.shape} (n_obs, n_var)")

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
    results_dir = f"spatial_analysis_results_{timestamp}"
    os.makedirs(results_dir, exist_ok=True)

# Create subdirectories for different analysis components
subdirs = [
    "01_data_loading",
    "02_stardist",
    "03_bin2cell",
]
for subdir in subdirs:
    os.makedirs(os.path.join(results_dir, subdir), exist_ok=True)
print(f"📂 Project structure created in: {results_dir}")
```

### Step 2 - Cellpose H&E Segmentation

**Assess Your Needs:**
- Do you need guidance on cellpose_he workflow?
- Are you familiar with omicverse cellpose functions?
- Do you have the required parameters (mpp, thresholds, etc.)?

**Path A - Get Official Template:**
```python
Spatial_Bin2Cell_Analysis(workflow_type="cellpose_he")
```
Then adapt the returned template to your data.

**Path B - Direct Implementation:**
```python
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_he)  # Check parameters first
# Then implement based on your data
```

**Goal:** Generate H&E-based cell segmentation labels

### Step 3 - Expand Labels

**Assess Your Needs:**
- Do you need guidance on label expansion?
- Do you know the optimal `max_bin_distance` for your data?
- Are you familiar with the expansion algorithm?

**Path A - Get Official Template:**
```python
Spatial_Bin2Cell_Analysis(workflow_type="expand_labels")
```
Then adapt the template with your specific parameters.

**Path B - Direct Implementation:**
```python
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_expand)  # Check parameters
# Implement with appropriate max_bin_distance
```

**Goal:** Expand cell labels to cover more spatial bins

### Step 4 - Cellpose GEX Segmentation

**Assess Your Needs:**
- Do you need guidance on GEX-based segmentation?
- Do you know the optimal `obs_key` for gene expression?
- Are you familiar with GEX segmentation parameters?

**Path A - Get Official Template:**
```python
Spatial_Bin2Cell_Analysis(workflow_type="cellpose_gex")
```
Then adapt with your GEX-specific settings.

**Path B - Direct Implementation:**
```python
import omicverse as ov
help(ov.space.visium_10x_hd_cellpose_gex)  # Check parameters
# Implement with appropriate obs_key and thresholds
```

**Goal:** Generate gene expression-based cell segmentation

### Step 5 - Salvage Secondary Labels

**Assess Your Needs:**
- Do you need guidance on combining HE and GEX labels?
- Do you know which labels to use as primary/secondary?
- Are you familiar with the salvage algorithm?

**Path A - Get Official Template:**
```python
Spatial_Bin2Cell_Analysis(workflow_type="salvage_secondary_labels")
```
Then adapt with your specific label keys.

**Path B - Direct Implementation:**
```python
import omicverse as ov
help(ov.space.salvage_secondary_labels)  # Check parameters
# Combine HE and GEX labels appropriately
```

**Goal:** Combine primary and secondary labels optimally

### Step 6 - Bin2Cell Conversion

**Assess Your Needs:**
- Do you need guidance on bin-to-cell conversion?
- Do you know the correct `labels_key` and `spatial_keys`?
- Are you familiar with the aggregation process?

**Path A - Get Official Template:**
```python
Spatial_Bin2Cell_Analysis(workflow_type="bin2cell")
```
Then adapt with your verified keys and parameters.

**Path B - Direct Implementation:**
```python
import omicverse as ov
help(ov.space.bin2cell)  # Check parameters
# Convert bins to cells using final labels
```

**Goal:** Convert spatial bins to cell-level data (cdata)

---

## Available Guidance Tools

### Spatial Template Engine (Optional)

- `Spatial_Bin2Cell_Analysis(workflow_type="cellpose_he")` - Get H&E segmentation template
- `Spatial_Bin2Cell_Analysis(workflow_type="expand_labels")` - Get label expansion template
- `Spatial_Bin2Cell_Analysis(workflow_type="cellpose_gex")` - Get GEX segmentation template
- `Spatial_Bin2Cell_Analysis(workflow_type="salvage_secondary_labels")` - Get label combination template
- `Spatial_Bin2Cell_Analysis(workflow_type="bin2cell")` - Get bin-to-cell conversion template

---

## Flexible Execution Strategy

1. **ASSESS:** Evaluate your current needs and knowledge level
2. **CHOOSE:** Use templates for guidance OR implement directly
3. **ADAPT:** Modify any template code to fit your specific data
4. **EXECUTE:** Run your adapted code with `run_python_code`
5. **PROGRESS:** Mark tasks complete and continue workflow

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

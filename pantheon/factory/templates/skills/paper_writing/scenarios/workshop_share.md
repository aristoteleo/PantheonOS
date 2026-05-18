---
id: workshop_share_scenario
name: "Workshop Share Scenario"
description: |
  Workflow for making complex methods learnable and reproducible through
  tutorial-style documentation and hands-on examples.
source: https://github.com/assafelovic/academic-research-skills
license: Apache 2.0
---

# Workshop Share Scenario

## When to Use

- User says: "Workshop", "tutorial", "教学", "hands-on session", "training material"
- Goal: Make complex methods accessible and reproducible
- Output: Tutorial document with step-by-step instructions and code examples

---

## Workflow Overview

```
Define Learning Objectives → Structure Tutorial → Write Step-by-Step Guide 
  → Create Code Examples → Add Troubleshooting → Generate Tutorial
```

---

## Detailed Steps

### Step 1: Define Learning Objectives

**Leader action**: Identify what learners should be able to do after the workshop

**Questions to answer**:
1. Who is the target audience? (beginners / intermediate / advanced)
2. What should they be able to do by the end?
3. What prerequisites do they need?
4. How long is the workshop? (1 hour / half-day / full-day)

**Deliverable**: `{workdir}/learning_objectives.md`

**Example**:
```markdown
# Learning Objectives

## Target Audience
Researchers with basic Python and single-cell analysis experience.

## By the end of this workshop, participants will be able to:
1. Install and set up AdaptiveHarmony
2. Load and preprocess single-cell data
3. Run batch correction with AdaptiveHarmony
4. Evaluate correction quality
5. Visualize and interpret results
6. Troubleshoot common issues

## Prerequisites
- Python 3.8+
- Basic familiarity with Scanpy
- Understanding of batch effects

## Duration
2 hours (1.5 hours instruction + 30 minutes hands-on)
```

---

### Step 2: Structure Tutorial

**Leader action**: Design tutorial structure based on learning objectives

**Tutorial structure options**:

**Option A: Linear Workflow**
1. Setup and Installation
2. Data Loading
3. Preprocessing
4. Batch Correction
5. Evaluation
6. Visualization
7. Troubleshooting

**Option B: Problem-Based**
1. The Problem: Batch Effects
2. Solution Overview
3. Hands-On: Correct Your Data
4. Advanced: Parameter Tuning
5. Real-World Example

**Option C: Modular**
1. Quick Start (15 min)
2. Detailed Walkthrough (45 min)
3. Advanced Topics (30 min)
4. FAQ and Troubleshooting (30 min)

**Leader decision**: Choose structure based on duration and audience.

---

### Step 3: Write Step-by-Step Guide

**Leader calls**: `writer`

**Instruction**:
```
Write tutorial document. Workdir: {workdir}.
Learning objectives: {workdir}/learning_objectives.md
Structure: {chosen structure}

Writing guidelines:
- Use second person ("you will...")
- Include code blocks for every step
- Add expected outputs
- Explain WHY, not just HOW
- Include common pitfalls
- Use screenshots/figures liberally

Deliverable: {workdir}/draft/tutorial.md
```

**Writing style for tutorials**:
- **Instructional**: Direct, imperative ("Run this command")
- **Explanatory**: Explain what each step does
- **Anticipatory**: Address common questions before they arise
- **Encouraging**: Positive tone, acknowledge difficulty

**Deliverable**: `{workdir}/draft/tutorial.md`

---

### Step 4: Create Code Examples

**Leader action**: Generate complete, runnable code examples

**Code example requirements**:
- **Complete**: Can be copy-pasted and run
- **Commented**: Explain each section
- **Realistic**: Use real or realistic data
- **Tested**: Verify all examples work
- **Modular**: Can be adapted to user's data

**Example types**:

**Type 1: Minimal Working Example**
```python
# Minimal example: batch correction in 5 lines
import scanpy as sc
from adaptive_harmony import correct_batches

# Load data
adata = sc.datasets.pbmc3k()

# Run batch correction
adata_corrected = correct_batches(adata, batch_key='batch')

# Visualize
sc.pl.umap(adata_corrected, color=['batch', 'cell_type'])
```

**Type 2: Complete Workflow**
```python
# Complete workflow with all steps
import scanpy as sc
import numpy as np
from adaptive_harmony import AdaptiveHarmony

# 1. Load data
adata = sc.read_h5ad('data.h5ad')
print(f"Loaded {adata.n_obs} cells, {adata.n_vars} genes")

# 2. Preprocessing
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var['mt'] = adata.var_names.str.startswith('MT-')
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], inplace=True)

# 3. Quality control
adata = adata[adata.obs.pct_counts_mt < 10, :]
print(f"After QC: {adata.n_obs} cells")

# 4. Normalization
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# 5. Feature selection
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
adata = adata[:, adata.var.highly_variable]

# 6. Batch correction
model = AdaptiveHarmony(theta=2, random_state=42)
adata_corrected = model.fit_transform(adata, batch_key='batch')

# 7. Evaluation
from adaptive_harmony.metrics import evaluate_correction
metrics = evaluate_correction(adata_corrected, batch_key='batch', 
                               celltype_key='cell_type')
print(f"Batch mixing ARI: {metrics['ari']:.3f}")
print(f"Marker preservation: {metrics['marker_preservation']:.1%}")

# 8. Visualization
sc.tl.pca(adata_corrected)
sc.pp.neighbors(adata_corrected)
sc.tl.umap(adata_corrected)
sc.pl.umap(adata_corrected, color=['batch', 'cell_type'], 
           save='corrected.png')
```

**Type 3: Troubleshooting Example**
```python
# Troubleshooting: correction not converging

# Problem: Method doesn't converge
# Solution: Adjust theta parameter

# Try different theta values
for theta in [1, 2, 5, 10]:
    model = AdaptiveHarmony(theta=theta, max_iter=20)
    adata_corrected = model.fit_transform(adata, batch_key='batch')
    print(f"Theta={theta}, converged: {model.converged_}")
```

**Deliverable**: `{workdir}/code_examples/` directory with `.py` files

---

### Step 5: Add Troubleshooting Section

**Leader action**: Anticipate common issues and provide solutions

**Troubleshooting format**:

```markdown
## Troubleshooting

### Issue 1: ImportError: No module named 'adaptive_harmony'

**Symptom**: 
```
ImportError: No module named 'adaptive_harmony'
```

**Cause**: Package not installed or wrong environment

**Solution**:
```bash
# Check if package is installed
pip list | grep adaptive-harmony

# If not installed
pip install adaptive-harmony

# If using conda
conda install -c conda-forge adaptive-harmony
```

### Issue 2: Correction not converging

**Symptom**: Warning message "Did not converge after 10 iterations"

**Cause**: Theta parameter too low or data has very strong batch effects

**Solution**:
1. Increase theta: `AdaptiveHarmony(theta=5)` instead of `theta=2`
2. Increase max iterations: `AdaptiveHarmony(max_iter=20)`
3. Check data quality: ensure preprocessing is correct

### Issue 3: Out of memory error

**Symptom**: `MemoryError` or kernel crash

**Cause**: Dataset too large for available RAM

**Solution**:
1. Subsample data: `adata = adata[np.random.choice(adata.n_obs, 50000), :]`
2. Use sparse matrices: `adata.X = scipy.sparse.csr_matrix(adata.X)`
3. Process in batches (see Advanced section)
```

**Deliverable**: Added to `{workdir}/draft/tutorial.md`

---

### Step 6: Generate Tutorial Document

**Leader calls**: `reporter`

**Instruction**:
```
Generate tutorial document. Workdir: {workdir}.
Source: {workdir}/draft/tutorial.md
Theme: tutorial (if available, else general_report)
Include: Code syntax highlighting, collapsible sections

Output: {workdir}/report/tutorial.html
```

**Deliverable**: `{workdir}/report/tutorial.html`

---

## Output Structure

```
{workdir}/
├── learning_objectives.md       # What learners will achieve
├── draft/
│   └── tutorial.md              # Complete tutorial
├── code_examples/
│   ├── 01_minimal.py            # Minimal working example
│   ├── 02_complete.py           # Complete workflow
│   ├── 03_advanced.py           # Advanced usage
│   └── data/                    # Example data
│       └── example.h5ad
└── report/
    └── tutorial.html            # Rendered tutorial
```

---

## Tutorial Template

```markdown
# AdaptiveHarmony Tutorial: Batch Correction for Single-Cell Data

**Duration**: 2 hours  
**Level**: Intermediate  
**Prerequisites**: Python, Scanpy basics

---

## Learning Objectives

By the end of this tutorial, you will be able to:
- [ ] Install and set up AdaptiveHarmony
- [ ] Run batch correction on your data
- [ ] Evaluate correction quality
- [ ] Troubleshoot common issues

---

## Setup (10 minutes)

### Installation

```bash
# Create conda environment
conda create -n harmony python=3.9
conda activate harmony

# Install dependencies
pip install scanpy adaptive-harmony
```

### Verify Installation

```python
import scanpy as sc
from adaptive_harmony import AdaptiveHarmony
print("Setup complete!")
```

**Expected output**: `Setup complete!`

---

## Part 1: Quick Start (15 minutes)

Let's start with a minimal example to see batch correction in action.

### Step 1: Load Example Data

```python
import scanpy as sc

# Load PBMC dataset (comes with Scanpy)
adata = sc.datasets.pbmc3k()
print(f"Loaded {adata.n_obs} cells")
```

**What this does**: Loads a small example dataset with 3,000 cells.

### Step 2: Add Artificial Batch Effects

```python
import numpy as np

# Simulate 2 batches
adata.obs['batch'] = np.random.choice(['batch1', 'batch2'], adata.n_obs)
```

**Why**: For demonstration, we add artificial batches. In real data, batches come from different experiments.

### Step 3: Run Batch Correction

```python
from adaptive_harmony import correct_batches

# Correct batches
adata_corrected = correct_batches(adata, batch_key='batch')
```

**What this does**: Removes batch effects while preserving biological variation.

### Step 4: Visualize Results

```python
# Before correction
sc.pp.pca(adata)
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.pl.umap(adata, color='batch', title='Before Correction')

# After correction
sc.pp.pca(adata_corrected)
sc.pp.neighbors(adata_corrected)
sc.tl.umap(adata_corrected)
sc.pl.umap(adata_corrected, color='batch', title='After Correction')
```

**Expected result**: Cells should mix by batch after correction.

---

## Part 2: Complete Workflow (45 minutes)

Now let's go through a complete analysis pipeline.

[Continue with detailed steps...]

---

## Part 3: Advanced Topics (30 minutes)

### Parameter Tuning

The `theta` parameter controls correction strength:
- Low theta (1-2): Gentle correction, preserves more biology
- High theta (5-10): Aggressive correction, removes more batch effects

```python
# Try different theta values
for theta in [1, 2, 5]:
    model = AdaptiveHarmony(theta=theta)
    adata_corrected = model.fit_transform(adata, batch_key='batch')
    # Evaluate...
```

### Working with Large Datasets

For datasets >100K cells:
```python
# Use approximate nearest neighbors
model = AdaptiveHarmony(approx_neighbors=True, n_neighbors=30)
```

---

## Troubleshooting

[See troubleshooting section above]

---

## Summary

You've learned:
- ✅ How to install and set up AdaptiveHarmony
- ✅ How to run batch correction
- ✅ How to evaluate results
- ✅ How to troubleshoot common issues

## Next Steps

- Try on your own data
- Read the paper for methodological details
- Join our community forum for questions

## Resources

- Documentation: https://adaptive-harmony.readthedocs.io
- GitHub: https://github.com/username/adaptive-harmony
- Paper: [Link to paper]
```

---

## Quality Checklist

Before finalizing tutorial:

- [ ] **Learning objectives are clear**
- [ ] **All code examples are tested and work**
- [ ] **Expected outputs are shown**
- [ ] **Common pitfalls are addressed**
- [ ] **Troubleshooting section is comprehensive**
- [ ] **Duration is realistic**
- [ ] **Prerequisites are stated**
- [ ] **Next steps are provided**

---

## Success Metrics

A successful workshop tutorial:
- Learners can complete all exercises
- Code examples run without errors
- Common questions are pre-answered
- Learners can apply method to their own data
- Positive feedback on clarity and completeness

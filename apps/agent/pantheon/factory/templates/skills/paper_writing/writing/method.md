---
id: method_writing
name: "Methods Writing Best Practices"
description: |
  Reproducibility checklist and essential details for methods sections.
  Based on Research-Paper-Writing-Skills.
source: https://github.com/Master-cai/Research-Paper-Writing-Skills
license: MIT
tags: [methods, writing, reproducibility, best-practices]
---

# Methods Writing Best Practices

Source: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills)

## Overview

The Methods section must provide sufficient detail for others to reproduce your work. Focus on **what you did** and **how you did it**, not why (that's in Introduction).

---

## Core Principle: Reproducibility

Every reader should be able to:
1. Obtain the same data
2. Run the same analysis
3. Reproduce the same results

---

## Essential Components

### 1. Data Sources

**What to include**:
- Dataset name and version
- Accession numbers (GEO, SRA, dbGaP, etc.)
- Sample size (number of samples, cells, patients)
- Data collection period (if applicable)
- Inclusion/exclusion criteria

**Example**:
> We analyzed single-cell RNA-seq data from the Human Cell Atlas (HCA) bone marrow dataset (accession: GSE12345, version 2.0). The dataset contains 50,000 cells from 10 healthy donors (5 male, 5 female, age 25-45). Cells with <200 detected genes or >10% mitochondrial content were excluded, resulting in 45,231 cells for downstream analysis.

**Checklist**:
- [ ] Dataset name and source
- [ ] Accession number or DOI
- [ ] Sample size (before and after QC)
- [ ] QC criteria

---

### 2. Software and Versions

**What to include**:
- Software name and version (exact version, not "latest")
- Programming language and version
- Key package versions
- Operating system (if relevant)

**Example**:
> All analyses were performed in Python 3.9.7 using Scanpy 1.9.1 [@wolf2018], NumPy 1.21.2 [@harris2020], and Pandas 1.3.3 [@mckinney2010]. Batch correction was performed using Harmony 0.1.1 [@korsunsky2019]. Computations were run on Ubuntu 20.04 with 64GB RAM.

**Checklist**:
- [ ] Python/R version
- [ ] Key package versions (at least top 3-5)
- [ ] Custom software with version/commit hash
- [ ] Hardware specs (if relevant for performance claims)

---

### 3. Preprocessing Steps

**What to include**:
- Quality control thresholds
- Normalization method
- Feature selection criteria
- Dimensionality reduction parameters

**Example**:
> Cells were filtered using the following criteria: 200 ≤ n_genes ≤ 5000, n_counts ≤ 30000, and pct_counts_mt ≤ 10%. Gene expression was normalized using the shifted logarithm method (log1p) after scaling to 10,000 counts per cell. Highly variable genes (HVGs) were selected using the Seurat v3 method [@stuart2019] with n_top_genes=2000. Principal component analysis (PCA) was performed on HVGs, retaining the top 50 components.

**Checklist**:
- [ ] QC thresholds with exact numbers
- [ ] Normalization method
- [ ] Feature selection method and parameters
- [ ] Dimensionality reduction method and parameters

---

### 4. Algorithm Parameters

**What to include**:
- All non-default parameters
- Random seeds (for reproducibility)
- Hyperparameter selection method (if applicable)

**Example**:
> Batch correction was performed using Harmony with the following parameters: theta=2, lambda=1, sigma=0.1, nclust=50, max_iter_harmony=10, and random_state=42. Clustering was performed using the Leiden algorithm [@traag2019] with resolution=0.5 and random_state=42.

**Checklist**:
- [ ] All non-default parameters listed
- [ ] Random seeds specified
- [ ] Hyperparameter tuning method (if used)

---

### 5. Statistical Methods

**What to include**:
- Statistical tests used
- Multiple testing correction method
- Significance thresholds
- Sample size justification (if applicable)

**Example**:
> Differential expression analysis was performed using a two-sided Wilcoxon rank-sum test. P-values were adjusted for multiple testing using the Benjamini-Hochberg method. Genes with adjusted p-value < 0.05 and |log2 fold change| > 1 were considered significantly differentially expressed.

**Checklist**:
- [ ] Statistical test name
- [ ] Multiple testing correction
- [ ] Significance threshold
- [ ] Effect size threshold (if applicable)

---

### 6. Computational Resources

**What to include** (if relevant for performance claims):
- Hardware specifications
- Runtime
- Memory usage

**Example**:
> All analyses were performed on a workstation with an Intel Xeon E5-2680 v4 CPU (2.40GHz, 28 cores) and 128GB RAM. The complete pipeline processed 100,000 cells in 15 minutes, using a peak memory of 32GB.

**When to include**: If you make performance claims or if computational cost is a concern.

---

## Structure Options

### Option 1: Chronological Order

Follow the analysis pipeline order:
1. Data acquisition
2. Quality control
3. Normalization
4. Batch correction
5. Clustering
6. Differential expression
7. Visualization

**When to use**: Standard analysis pipelines.

---

### Option 2: Grouped by Category

Group related methods together:
1. **Data and Preprocessing**: Data sources, QC, normalization
2. **Batch Correction**: Algorithm, parameters
3. **Clustering and Annotation**: Methods, parameters
4. **Differential Expression**: Statistical tests
5. **Visualization**: Tools and parameters

**When to use**: Complex analyses with multiple components.

---

### Option 3: Subsections by Analysis

Separate subsection for each major analysis:
- **3.1 Data Preprocessing**
- **3.2 Batch Correction**
- **3.3 Cell Type Annotation**
- **3.4 Differential Expression Analysis**

**When to use**: Papers with multiple distinct analyses.

---

## Writing Style

### Use Past Tense

Methods describe what you did, not what you will do or what the method does.

❌ **Bad**: "We will normalize the data using log1p."
✅ **Good**: "We normalized the data using log1p."

---

### Be Specific, Not Vague

❌ **Bad**: "We used standard preprocessing."
✅ **Good**: "We filtered cells with <200 genes and normalized using log1p."

---

### Use Active Voice

❌ **Bad**: "The data was normalized."
✅ **Good**: "We normalized the data."

---

### Include Exact Commands (Optional)

For complex or custom analyses, include exact commands:

> Clustering was performed using the following command:
> ```python
> sc.tl.leiden(adata, resolution=0.5, random_state=42)
> ```

**When to include**: Custom pipelines or non-standard parameters.

---

## Reproducibility Checklist

Before finalizing Methods section, verify:

### Data
- [ ] Dataset name and accession number
- [ ] Sample size (before and after QC)
- [ ] Data availability statement

### Software
- [ ] Python/R version
- [ ] Key package versions (top 5)
- [ ] Custom code availability (GitHub link)

### Parameters
- [ ] All non-default parameters listed
- [ ] Random seeds specified
- [ ] QC thresholds with exact numbers

### Statistical Methods
- [ ] Statistical tests named
- [ ] Multiple testing correction specified
- [ ] Significance thresholds stated

### Reproducibility Aids
- [ ] Code availability statement
- [ ] Data availability statement
- [ ] Environment specification (requirements.txt, conda env)

---

## Data and Code Availability

**Always include** at the end of Methods or as a separate section:

**Example**:
> **Data Availability**: The processed data and analysis results are available at Zenodo (DOI: 10.5281/zenodo.1234567). Raw sequencing data are available at GEO (accession: GSE12345).
>
> **Code Availability**: All analysis code is available at GitHub (https://github.com/username/repo) with detailed documentation and example notebooks. The analysis environment can be reproduced using the provided conda environment file (environment.yml).

---

## Common Mistakes to Avoid

### 1. Missing Version Numbers

❌ **Bad**: "We used Scanpy for analysis."
✅ **Good**: "We used Scanpy 1.9.1 for analysis."

### 2. Vague Parameters

❌ **Bad**: "We used default parameters."
✅ **Good**: "We used resolution=0.5 for clustering."

### 3. No Random Seeds

❌ **Bad**: "We performed PCA."
✅ **Good**: "We performed PCA with random_state=42."

### 4. Missing QC Thresholds

❌ **Bad**: "We filtered low-quality cells."
✅ **Good**: "We filtered cells with <200 genes or >10% mitochondrial content."

### 5. No Data Availability

❌ **Bad**: (No mention of data availability)
✅ **Good**: "Data are available at GEO (GSE12345)."

---

## Template for Quick Start

```markdown
## Methods

### Data Sources
We analyzed {dataset name} (accession: {accession number}, version {version}). The dataset contains {n} samples/cells from {description}. {Inclusion/exclusion criteria}.

### Preprocessing
Cells were filtered using the following criteria: {QC thresholds}. Gene expression was normalized using {method} after scaling to {n} counts per cell. Highly variable genes were selected using {method} with {parameters}. Principal component analysis was performed, retaining the top {n} components.

### {Analysis Name}
{Analysis} was performed using {software} {version} with the following parameters: {parameters}. Random seed was set to {seed} for reproducibility.

### Statistical Analysis
{Statistical test} was used to {purpose}. P-values were adjusted using {correction method}. {Significance threshold}.

### Data and Code Availability
The processed data are available at {repository} ({accession}). All analysis code is available at {GitHub link}.
```

---

## Example: Complete Methods Section

> ## Methods
>
> ### Data Sources
> We analyzed single-cell RNA-seq data from the Human Cell Atlas bone marrow dataset (GEO accession: GSE12345, version 2.0). The dataset contains 50,000 cells from 10 healthy donors (5 male, 5 female, age 25-45). Cells with <200 detected genes or >10% mitochondrial content were excluded, resulting in 45,231 cells for downstream analysis.
>
> ### Preprocessing
> All analyses were performed in Python 3.9.7 using Scanpy 1.9.1. Gene expression was normalized using the shifted logarithm method (log1p) after scaling to 10,000 counts per cell. Highly variable genes (HVGs) were selected using the Seurat v3 method with n_top_genes=2000. Principal component analysis (PCA) was performed on HVGs, retaining the top 50 components.
>
> ### Batch Correction
> Batch correction was performed using Harmony 0.1.1 with theta=2, lambda=1, sigma=0.1, nclust=50, max_iter_harmony=10, and random_state=42.
>
> ### Clustering and Annotation
> Clustering was performed using the Leiden algorithm with resolution=0.5 and random_state=42. Cell types were annotated using marker genes from PanglaoDB.
>
> ### Differential Expression Analysis
> Differential expression analysis was performed using a two-sided Wilcoxon rank-sum test. P-values were adjusted using the Benjamini-Hochberg method. Genes with adjusted p-value < 0.05 and |log2 fold change| > 1 were considered significantly differentially expressed.
>
> ### Data and Code Availability
> The processed data are available at Zenodo (DOI: 10.5281/zenodo.1234567). All analysis code is available at GitHub (https://github.com/username/repo).

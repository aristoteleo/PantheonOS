---
id: results_writing
name: "Results Writing Best Practices"
description: |
  Guidelines for presenting experimental results with proper evidence.
  Based on Research-Paper-Writing-Skills.
source: https://github.com/Master-cai/Research-Paper-Writing-Skills
license: MIT
tags: [results, writing, figures, evidence, best-practices]
---

# Results Writing Best Practices

Source: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills)

## Overview

The Results section presents your findings objectively. Lead with claims, follow with evidence (figures/tables). Use past tense and avoid interpretation (save for Discussion).

---

## Core Principle: Claim → Evidence

Every subsection should follow this pattern:
1. **Claim**: State what you found (1 sentence)
2. **Evidence**: Reference figure/table that supports it
3. **Details**: Describe the evidence (1-2 sentences)

---

## Structure

### Subsection Organization

Organize results by research question or analysis type:

**Option 1: By Research Question**
- 4.1 Dataset Quality and Preprocessing
- 4.2 Batch Correction Performance
- 4.3 Rare Cell Type Preservation
- 4.4 Computational Efficiency

**Option 2: By Analysis Type**
- 4.1 Benchmark Results
- 4.2 Case Study: Human Cell Atlas
- 4.3 Ablation Study

**Option 3: Chronological**
- 4.1 Data Characteristics
- 4.2 Method Validation
- 4.3 Comparison with Baselines
- 4.4 Real-World Application

---

## Writing Pattern

### Pattern 1: Claim → Figure → Details

**Example**:
> AdaptiveHarmony achieves superior batch mixing while preserving biological variation (Figure 3A). On the PBMC dataset, AdaptiveHarmony achieves 0.92 ARI for batch mixing, outperforming Harmony (0.85 ARI) and Seurat CCA (0.82 ARI) by 8% and 12% respectively. Importantly, AdaptiveHarmony preserves 95% of cell-type-specific marker genes, compared to 78% for Harmony and 81% for Seurat CCA (Figure 3B).

**Structure**:
- Sentence 1: Claim + figure reference
- Sentence 2-3: Quantitative details from figure
- Sentence 4: Additional insight from figure

---

### Pattern 2: Multiple Claims, One Figure

**Example**:
> We evaluated AdaptiveHarmony on 10 benchmark datasets (Figure 4). Across all datasets, AdaptiveHarmony consistently outperforms baselines in both batch mixing (mean ARI: 0.89 vs. 0.82 for Harmony) and biological conservation (mean silhouette score: 0.71 vs. 0.65 for Harmony). The improvement is most pronounced on datasets with rare cell types (Figure 4C), where AdaptiveHarmony achieves 15-20% better marker preservation.

---

### Pattern 3: Figure → Multiple Insights

**Example**:
> Figure 5 shows the application of AdaptiveHarmony to the Human Cell Atlas bone marrow dataset. The method successfully integrates 10 batches from different donors (Figure 5A), revealing clear cell type clusters (Figure 5B). Notably, we identified a rare population of early erythroid progenitors (0.3% of cells) that was not detected by standard methods (Figure 5C). This population expresses known erythroid markers (HBA1, HBA2) and is enriched in donor 3 (Figure 5D).

---

## Figure/Table Requirements

### Every Subsection Needs ≥1 Figure/Table

**Rule**: Each subsection must reference at least one figure or table.

**Why**: Results without visual evidence are not convincing.

**Example**:
- ❌ **Bad**: "Our method performs well on benchmark datasets."
- ✅ **Good**: "Our method achieves 0.92 ARI on benchmark datasets (Figure 3)."

---

### Figure References

**Use cross-references**:
- Markdown: `(Figure @fig:performance)` or `(Figure 3)`
- LaTeX: `(Figure~\ref{fig:performance})`

**Reference style**:
- First mention: "Figure 3A shows..."
- Subsequent: "As shown in Figure 3A..."
- Parenthetical: "...achieves 0.92 ARI (Figure 3A)."

---

### Figure Captions

**Structure**:
1. **Title sentence**: What does the figure show?
2. **Panel descriptions**: (A) ..., (B) ..., (C) ...
3. **Key findings**: Highlight main takeaways
4. **Technical details**: Sample sizes, error bars, statistical tests

**Example**:
> **Figure 3. AdaptiveHarmony achieves superior batch correction performance.** (A) UMAP visualization of PBMC dataset before and after batch correction. (B) Quantitative comparison of batch mixing (ARI) and biological conservation (silhouette score) across methods. (C) Marker gene preservation for rare cell types. AdaptiveHarmony (red) outperforms Harmony (blue) and Seurat CCA (green) on all metrics. Error bars represent standard deviation across 5 random seeds. *p < 0.05, **p < 0.01, ***p < 0.001 (two-sided t-test).

---

## Writing Style

### Use Past Tense

Results describe what you observed, not general truths.

❌ **Bad**: "The method achieves 0.92 ARI."
✅ **Good**: "The method achieved 0.92 ARI."

---

### Be Objective

Avoid interpretation and subjective language in Results.

❌ **Bad**: "Surprisingly, our method performs well."
✅ **Good**: "Our method achieved 0.92 ARI."

(Save "surprisingly" for Discussion)

---

### Use Concrete Numbers

Avoid vague quantifiers.

❌ **Bad**: "Our method performs significantly better."
✅ **Good**: "Our method achieved 0.92 ARI, 8% better than Harmony (0.85 ARI)."

---

### Avoid Redundancy with Figures

Don't repeat everything in the figure caption.

❌ **Bad**: "Figure 3A shows UMAP before correction. Figure 3B shows UMAP after correction. Figure 3C shows quantitative metrics."
✅ **Good**: "Figure 3 shows batch correction performance. Before correction, cells cluster by batch (Figure 3A). After correction, cells cluster by cell type (Figure 3B), achieving 0.92 ARI (Figure 3C)."

---

## Subsection Template

```markdown
### 4.X {Subsection Title}

{Claim sentence with figure reference}. {Quantitative details from figure}. {Additional insights from figure}.

{Optional: Second claim with evidence}. {Details}.

{Optional: Comparison or context}. {Details}.
```

**Example**:

```markdown
### 4.2 Batch Correction Performance

AdaptiveHarmony achieves superior batch mixing while preserving biological variation (Figure 3). On the PBMC dataset, AdaptiveHarmony achieved 0.92 ARI for batch mixing, outperforming Harmony (0.85 ARI) and Seurat CCA (0.82 ARI) by 8% and 12% respectively. Importantly, AdaptiveHarmony preserved 95% of cell-type-specific marker genes, compared to 78% for Harmony and 81% for Seurat CCA.

We validated this performance across 10 benchmark datasets (Figure 4). AdaptiveHarmony consistently outperformed baselines in both batch mixing (mean ARI: 0.89 vs. 0.82 for Harmony) and biological conservation (mean silhouette score: 0.71 vs. 0.65 for Harmony). The improvement was most pronounced on datasets with rare cell types, where AdaptiveHarmony achieved 15-20% better marker preservation.
```

---

## Common Patterns

### Pattern: Benchmark Results

**Structure**:
1. Overall performance summary
2. Comparison with baselines
3. Performance on specific subsets

**Example**:
> We evaluated AdaptiveHarmony on 10 benchmark datasets spanning different tissues and technologies (Figure 4A). AdaptiveHarmony achieved the highest mean ARI (0.89) across all datasets, outperforming Harmony (0.82), Seurat CCA (0.80), and scVI (0.85). The improvement was consistent across different dataset sizes (Figure 4B) and batch numbers (Figure 4C). On datasets with rare cell types (<1% frequency), AdaptiveHarmony achieved 18% better marker preservation than the next best method (Figure 4D).

---

### Pattern: Case Study

**Structure**:
1. Dataset description
2. Analysis results
3. Biological insights

**Example**:
> We applied AdaptiveHarmony to the Human Cell Atlas bone marrow dataset (50,000 cells, 10 donors). The method successfully integrated all batches (Figure 5A), revealing 15 distinct cell types (Figure 5B). Notably, we identified a rare population of early erythroid progenitors (0.3% of cells, n=150) that expressed HBA1, HBA2, and GYPA (Figure 5C). This population was enriched in donor 3 (0.8% vs. 0.2% in other donors, p < 0.001, Figure 5D).

---

### Pattern: Ablation Study

**Structure**:
1. Component being tested
2. Performance with/without component
3. Conclusion about component importance

**Example**:
> To assess the contribution of cell-type-specific correction, we compared AdaptiveHarmony with a uniform correction baseline (Figure 6). Removing cell-type-specific correction reduced ARI from 0.92 to 0.85 (7% decrease) and marker preservation from 95% to 82% (13% decrease). This demonstrates that cell-type-specific correction is critical for preserving rare cell types.

---

## Quality Checklist

Before finalizing Results section:

- [ ] **Every subsection has ≥1 figure/table reference**
- [ ] **Claims lead, evidence follows**
- [ ] **Concrete numbers, not vague quantifiers**
- [ ] **Past tense throughout**
- [ ] **No interpretation** (save for Discussion)
- [ ] **Figure captions are complete**
- [ ] **Statistical significance reported** (p-values, error bars)
- [ ] **Comparisons are quantitative** (not just "better")

---

## Common Mistakes to Avoid

### 1. Results Without Figures

❌ **Bad**: "Our method performs well on benchmark datasets."
✅ **Good**: "Our method achieved 0.92 ARI on benchmark datasets (Figure 3)."

### 2. Vague Quantifiers

❌ **Bad**: "Our method is significantly better."
✅ **Good**: "Our method achieved 8% higher ARI (p < 0.001)."

### 3. Interpretation in Results

❌ **Bad**: "Surprisingly, our method performs well, suggesting that..."
✅ **Good**: "Our method achieved 0.92 ARI." (Move interpretation to Discussion)

### 4. Missing Statistical Tests

❌ **Bad**: "Our method is better than Harmony."
✅ **Good**: "Our method achieved 0.92 ARI vs. 0.85 for Harmony (p < 0.001, two-sided t-test)."

### 5. Redundant Figure Descriptions

❌ **Bad**: "Figure 3A shows X. Figure 3B shows Y. Figure 3C shows Z."
✅ **Good**: "Figure 3 shows batch correction performance across three metrics (A-C)."

---

## Example: Complete Results Section

> ## Results
>
> ### 4.1 Dataset Characteristics
>
> We evaluated AdaptiveHarmony on 10 benchmark single-cell RNA-seq datasets (Table 1). The datasets span different tissues (bone marrow, PBMC, brain), technologies (10x, Smart-seq2, Drop-seq), and batch numbers (2-10 batches). Dataset sizes range from 5,000 to 100,000 cells, with rare cell type frequencies from 0.1% to 5%.
>
> ### 4.2 Batch Correction Performance
>
> AdaptiveHarmony achieved superior batch mixing while preserving biological variation (Figure 3). On the PBMC dataset, AdaptiveHarmony achieved 0.92 ARI for batch mixing, outperforming Harmony (0.85 ARI) and Seurat CCA (0.82 ARI) by 8% and 12% respectively (Figure 3A). Importantly, AdaptiveHarmony preserved 95% of cell-type-specific marker genes, compared to 78% for Harmony and 81% for Seurat CCA (Figure 3B).
>
> We validated this performance across all 10 benchmark datasets (Figure 4). AdaptiveHarmony consistently outperformed baselines in both batch mixing (mean ARI: 0.89 vs. 0.82 for Harmony, p < 0.001) and biological conservation (mean silhouette score: 0.71 vs. 0.65 for Harmony, p < 0.001). The improvement was most pronounced on datasets with rare cell types, where AdaptiveHarmony achieved 15-20% better marker preservation (Figure 4C).
>
> ### 4.3 Rare Cell Type Preservation
>
> To specifically assess rare cell type preservation, we focused on cell populations with <1% frequency (Figure 5). AdaptiveHarmony preserved 92% of rare cell type markers on average, compared to 75% for Harmony and 78% for Seurat CCA (Figure 5A). This improvement was consistent across different rare cell types, including early progenitors, transitional states, and tissue-resident populations (Figure 5B).
>
> ### 4.4 Computational Efficiency
>
> AdaptiveHarmony processed 100,000 cells in 8 minutes on a standard workstation (Figure 6A), comparable to Harmony (7 minutes) and faster than scVI (25 minutes). Memory usage scaled linearly with cell number (Figure 6B), requiring 4GB for 100,000 cells.

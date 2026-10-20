---
id: discussion_writing
name: "Discussion Writing Best Practices"
description: |
  Structure for interpretation, comparison, limitations, and future work.
  Based on Research-Paper-Writing-Skills.
source: https://github.com/Master-cai/Research-Paper-Writing-Skills
license: MIT
tags: [discussion, writing, interpretation, best-practices]
---

# Discussion Writing Best Practices

Source: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills)

## Overview

The Discussion interprets your results, compares with prior work, acknowledges limitations, and suggests future directions. Use present tense for general findings and past tense for specific results.

---

## Four-Part Structure

```
Interpretation → Comparison → Limitations → Future Work
```

---

## Part 1: Interpretation (2-3 paragraphs)

### Purpose
Explain what your results mean and why they matter.

### Content
1. **Restate main findings** (1 paragraph)
2. **Interpret findings** (1-2 paragraphs)
3. **Connect to broader context** (1 paragraph)

### Example

**Paragraph 1: Restate Main Findings**
> We developed AdaptiveHarmony, a batch correction method that applies cell-type-specific correction strengths. On 10 benchmark datasets, AdaptiveHarmony achieved superior performance in both batch mixing (mean ARI: 0.89) and biological conservation (mean silhouette: 0.71), outperforming existing methods by 8-15%. Importantly, AdaptiveHarmony preserved 95% of rare cell type markers, addressing a key limitation of current approaches.

**Paragraph 2: Interpret Findings**
> These results demonstrate that cell-type-specific correction is critical for preserving rare cell types during batch correction. Our finding that rare cells require less aggressive correction aligns with the observation that rare populations have fewer neighbors for correction algorithms to leverage. By automatically estimating optimal correction strength for each cell type, AdaptiveHarmony balances the trade-off between batch effect removal and biological variation preservation.

**Paragraph 3: Broader Context**
> This work addresses a fundamental challenge in single-cell data integration: how to correct batch effects without losing rare but biologically important cell populations. As single-cell atlases grow to include millions of cells from hundreds of batches, methods that can reliably preserve rare cell types will be essential for discovering novel cell states and understanding cellular heterogeneity.

---

## Part 2: Comparison with Prior Work (1-2 paragraphs)

### Purpose
Position your work relative to existing methods.

### Content
1. **How your approach differs** (1 paragraph)
2. **Why your approach is better** (1 paragraph)
3. **When existing methods might be preferred** (optional)

### Example

**Paragraph 1: How Your Approach Differs**
> Unlike existing batch correction methods that apply uniform correction across all cells (Harmony, Seurat CCA) or require manual parameter tuning (scVI, scANVI), AdaptiveHarmony automatically estimates cell-type-specific correction strengths. This differs from recent work on rare cell preservation (RareCorrect) which requires pre-defined rare cell labels, whereas AdaptiveHarmony operates in an unsupervised manner.

**Paragraph 2: Why Your Approach is Better**
> Our approach offers several advantages. First, the automatic estimation of correction strengths eliminates the need for manual parameter tuning, making the method more accessible to non-expert users. Second, the cell-type-specific correction preserves rare cell types more effectively than uniform correction (95% vs. 78% marker preservation). Third, the unsupervised nature of the method makes it applicable to datasets where rare cell types are unknown a priori.

**Paragraph 3: When Existing Methods Might Be Preferred (Optional)**
> However, for datasets without rare cell types or with very strong batch effects, uniform correction methods like Harmony may be sufficient and computationally faster. Additionally, for datasets with known cell type labels, supervised methods like scANVI may achieve comparable performance with better interpretability.

---

## Part 3: Limitations (1 paragraph, MANDATORY)

### Purpose
Acknowledge weaknesses and boundary conditions of your work.

### Content
1. **Technical limitations** (what your method cannot do)
2. **Scope limitations** (what you did not test)
3. **Assumptions** (what your method assumes)

### Example

> Our work has several limitations. First, AdaptiveHarmony assumes that cell types are separable in the uncorrected data; if batch effects completely obscure cell type structure, the method may fail to estimate appropriate correction strengths. Second, we evaluated the method primarily on scRNA-seq data; performance on other modalities (scATAC-seq, spatial transcriptomics) remains to be tested. Third, the method's computational cost scales quadratically with cell number, which may limit applicability to datasets with >1 million cells. Finally, while we demonstrated improved rare cell type preservation, we did not validate the biological relevance of all identified rare populations through experimental follow-up.

### Key Phrases
- "Our work has several limitations."
- "First, ... Second, ... Third, ..."
- "While we demonstrated X, we did not test Y."
- "The method assumes..."
- "Performance on X remains to be evaluated."

---

## Part 4: Future Work (1 paragraph)

### Purpose
Suggest directions for extending or improving your work.

### Content
1. **Technical improvements** (how to address limitations)
2. **New applications** (where else the method could be used)
3. **Open questions** (what remains to be understood)

### Example

> Several directions for future work emerge from this study. First, extending AdaptiveHarmony to handle extremely large datasets (>1 million cells) through approximate nearest neighbor methods or mini-batch processing would broaden its applicability. Second, adapting the method for other single-cell modalities (scATAC-seq, CITE-seq, spatial transcriptomics) would enable multi-modal integration with rare cell type preservation. Third, incorporating biological prior knowledge (e.g., cell type hierarchies, developmental trajectories) could further improve correction strength estimation. Finally, experimental validation of rare cell populations identified through improved batch correction would strengthen confidence in the biological relevance of these findings.

### Key Phrases
- "Several directions for future work..."
- "First, extending... Second, adapting... Third, incorporating..."
- "would enable..."
- "would improve..."
- "remains an open question."

---

## Writing Style

### Use Present Tense for General Findings

❌ **Bad**: "Our results showed that rare cells required less correction."
✅ **Good**: "Our results show that rare cells require less correction."

### Use Past Tense for Specific Results

❌ **Bad**: "We achieve 0.92 ARI on the PBMC dataset."
✅ **Good**: "We achieved 0.92 ARI on the PBMC dataset."

### Balance Confidence and Humility

❌ **Too confident**: "Our method solves the batch correction problem."
✅ **Balanced**: "Our method addresses a key limitation of current batch correction approaches."

❌ **Too humble**: "Our method might possibly be useful in some cases."
✅ **Balanced**: "Our method improves rare cell type preservation by 15-20%."

### Avoid Hedging Ladders

❌ **Bad**: "Our results may potentially possibly suggest that..."
✅ **Good**: "Our results suggest that..."

---

## Quality Checklist

Before finalizing Discussion:

- [ ] **Interpretation**: Main findings restated and interpreted
- [ ] **Comparison**: Positioned relative to prior work
- [ ] **Limitations**: At least 3-4 limitations acknowledged
- [ ] **Future work**: Concrete directions suggested
- [ ] **No new results**: Discussion interprets existing results, doesn't introduce new ones
- [ ] **Balanced tone**: Confident but not overreaching
- [ ] **Proper tenses**: Present for general, past for specific

---

## Common Mistakes to Avoid

### 1. No Limitations Paragraph

❌ **Bad**: (No limitations mentioned)
✅ **Good**: "Our work has several limitations. First, ..."

**Why it matters**: Reviewers will find limitations anyway. Better to acknowledge them proactively.

---

### 2. Introducing New Results

❌ **Bad**: "We also tested method X on dataset Y and found..."
✅ **Good**: (Put new results in Results section, or remove)

**Why it matters**: Discussion interprets existing results, doesn't present new ones.

---

### 3. Vague Future Work

❌ **Bad**: "Future work could improve the method."
✅ **Good**: "Extending the method to handle >1M cells through approximate nearest neighbors would broaden applicability."

**Why it matters**: Specific suggestions are more useful than vague statements.

---

### 4. Overclaiming

❌ **Bad**: "Our method solves the batch correction problem."
✅ **Good**: "Our method addresses the challenge of preserving rare cell types during batch correction."

**Why it matters**: Overclaiming invites skepticism and rejection.

---

### 5. Missing Comparison

❌ **Bad**: (No comparison with prior work)
✅ **Good**: "Unlike Harmony which applies uniform correction, our method uses cell-type-specific strengths."

**Why it matters**: Reviewers need to understand how your work differs from and improves upon existing methods.

---

## Template for Quick Start

```markdown
## Discussion

{Restate main findings in 2-3 sentences}. {Interpret what findings mean}. {Connect to broader context}.

{How your approach differs from prior work}. {Why your approach is better}. {Optional: When existing methods might be preferred}.

Our work has several limitations. First, {limitation 1}. Second, {limitation 2}. Third, {limitation 3}. {Optional: Fourth, limitation 4}.

Several directions for future work emerge from this study. First, {direction 1} would {benefit}. Second, {direction 2} would {benefit}. Third, {direction 3} would {benefit}.
```

---

## Example: Complete Discussion Section

> ## Discussion
>
> We developed AdaptiveHarmony, a batch correction method that applies cell-type-specific correction strengths. On 10 benchmark datasets, AdaptiveHarmony achieved superior performance in both batch mixing (mean ARI: 0.89) and biological conservation (mean silhouette: 0.71), outperforming existing methods by 8-15%. Importantly, AdaptiveHarmony preserved 95% of rare cell type markers, addressing a key limitation of current approaches.
>
> These results demonstrate that cell-type-specific correction is critical for preserving rare cell types during batch correction. Our finding that rare cells require less aggressive correction aligns with the observation that rare populations have fewer neighbors for correction algorithms to leverage. By automatically estimating optimal correction strength for each cell type, AdaptiveHarmony balances the trade-off between batch effect removal and biological variation preservation. This work addresses a fundamental challenge in single-cell data integration: how to correct batch effects without losing rare but biologically important cell populations.
>
> Unlike existing batch correction methods that apply uniform correction across all cells (Harmony, Seurat CCA) or require manual parameter tuning (scVI, scANVI), AdaptiveHarmony automatically estimates cell-type-specific correction strengths. This differs from recent work on rare cell preservation (RareCorrect) which requires pre-defined rare cell labels, whereas AdaptiveHarmony operates in an unsupervised manner. Our approach offers several advantages: automatic parameter estimation eliminates manual tuning, cell-type-specific correction preserves rare cell types more effectively (95% vs. 78% marker preservation), and the unsupervised nature makes it applicable when rare cell types are unknown a priori.
>
> Our work has several limitations. First, AdaptiveHarmony assumes that cell types are separable in the uncorrected data; if batch effects completely obscure cell type structure, the method may fail. Second, we evaluated the method primarily on scRNA-seq data; performance on other modalities (scATAC-seq, spatial transcriptomics) remains to be tested. Third, the method's computational cost scales quadratically with cell number, limiting applicability to datasets with >1 million cells. Finally, while we demonstrated improved rare cell type preservation, we did not validate the biological relevance of all identified rare populations through experimental follow-up.
>
> Several directions for future work emerge from this study. First, extending AdaptiveHarmony to handle extremely large datasets (>1 million cells) through approximate nearest neighbor methods would broaden its applicability. Second, adapting the method for other single-cell modalities (scATAC-seq, CITE-seq, spatial transcriptomics) would enable multi-modal integration with rare cell type preservation. Third, incorporating biological prior knowledge (e.g., cell type hierarchies, developmental trajectories) could further improve correction strength estimation. Finally, experimental validation of rare cell populations identified through improved batch correction would strengthen confidence in the biological relevance of these findings.

---

## Advanced: Structuring Complex Discussions

For papers with multiple contributions or complex findings, consider subsections:

### Option 1: By Contribution
- **5.1 Cell-Type-Specific Correction**
- **5.2 Rare Cell Type Preservation**
- **5.3 Computational Efficiency**
- **5.4 Limitations and Future Work**

### Option 2: By Theme
- **5.1 Interpretation of Main Findings**
- **5.2 Comparison with Existing Methods**
- **5.3 Implications for Single-Cell Atlas Construction**
- **5.4 Limitations and Future Directions**

**When to use subsections**: Papers with >3 major findings or >5 pages of discussion.

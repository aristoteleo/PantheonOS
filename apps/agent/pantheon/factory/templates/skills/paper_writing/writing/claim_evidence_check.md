---
id: claim_evidence_check
name: "Claim-Evidence + Citation Grounding Check"
description: |
  Combined audit: every major claim in Abstract and Introduction must have
  supporting evidence (citation, experimental result, or figure), and every
  citation must actually support the claim it is used for. Includes
  sentence-level reranking and attribution rules.
source: https://github.com/Master-cai/Research-Paper-Writing-Skills + https://github.com/nature-citation
license: MIT
tags: [quality-check, claims, evidence, citation, best-practices]
---

# Claim-Evidence + Citation Grounding Check

Sources: [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills),
nature-citation grounding practices, OpenScholar / PaperQA attribution.

## Purpose

Two-sided audit:

1. **Claim side**: every major claim has supporting evidence (avoid "空口无凭" empty claims).
2. **Citation side**: every citation actually supports the claim it is attached to (avoid misattribution and overclaiming).

Run together — they share the same draft pass.

---

## Protocol

### Step 1: Extract Claims

Read Abstract and Introduction. Extract all major claims:
- **Factual claims**: "X is a major challenge"
- **Performance claims**: "Our method improves accuracy by 18%"
- **Novelty claims**: "This is the first work to..."
- **Methodological claims**: "Our approach uses X to achieve Y"

**What to skip**:
- Background statements that are common knowledge
- Transition sentences
- Paper roadmap statements

---

### Step 2: Classify Claim Type

For each claim, determine required evidence type:

| Claim Type | Required Evidence | Example |
|------------|-------------------|---------|
| **Factual** | Citation from literature | "Batch effects are a major challenge [@smith2023]" |
| **Performance** | Experimental results (table/figure) | "Our method achieves 0.92 ARI (Table 3)" |
| **Novelty** | Literature search showing no prior work | "This is the first work to apply X to Y (see Related Work)" |
| **Methodological** | Description in Methods section | "We use graph neural networks (see Methods 3.2)" |
| **Comparative** | Quantitative comparison | "15% better than baseline [@jones2024] (Figure 4)" |

---

### Step 3: Check Evidence

For each claim, search for supporting evidence in:

1. **references.bib**: Check if citation exists
2. **Results section**: Check if data/figure/table exists
3. **Methods section**: Check if methodology is described
4. **Related Work**: Check if novelty is justified

Rate alignment:
- ✅ **Supported**: Clear evidence present and strong
- ⚠️ **Weak**: Evidence exists but not strong enough or indirect
- ❌ **Unsupported**: No evidence found

---

### Step 4: Generate Report

Output format:

```markdown
# Claim-Evidence Alignment Report

## Abstract Claims

| # | Claim | Type | Evidence | Status | Action |
|---|-------|------|----------|--------|--------|
| 1 | "Our method improves accuracy by 18%" | Performance | Table 3, Line 234 | ✅ Supported | None |
| 2 | "This is the first work on X" | Novelty | No citation | ❌ Unsupported | Add literature search or weaken claim |
| 3 | "Batch effects are a major challenge" | Factual | [@smith2023] | ✅ Supported | None |
| 4 | "The approach is efficient" | Performance | Figure 2 (runtime) | ⚠️ Weak | Add quantitative comparison |

## Introduction Claims

| # | Claim | Type | Evidence | Status | Action |
|---|-------|------|----------|--------|--------|
| 5 | "Existing methods face a trade-off" | Factual | [@jones2024; @lee2023] | ✅ Supported | None |
| 6 | "Rare cell types are overcorrected" | Factual | No citation | ❌ Unsupported | Add citation or experimental evidence |
| 7 | "Our method uses graph-based approach" | Methodological | Methods 3.2 | ✅ Supported | None |

## Summary Statistics

- **Total claims**: 12
- **Supported**: 8 (67%)
- **Weak support**: 2 (17%)
- **Unsupported**: 2 (17%)
- **Alignment rate**: 83% (Supported + Weak)

## Action Items

### Critical (Must Fix)
1. **Claim #2**: Add literature search to justify "first work" or change to "one of the first works"
2. **Claim #6**: Add citation [@wang2022] or add experimental evidence in Results

### Recommended (Should Fix)
3. **Claim #4**: Add quantitative comparison (e.g., "2x faster than baseline")

## Quality Gate Status

- **Target**: ≥80% alignment (Supported + Weak)
- **Current**: 83%
- **Status**: ✅ PASS
```

---

### Step 5: Revision

If alignment < 80%:

1. **Add missing citations**:
   - Search literature for supporting evidence
   - Add to references.bib
   - Insert citation in text

2. **Add experimental evidence**:
   - Run additional experiments if needed
   - Add results to Results section
   - Reference in claim

3. **Weaken unsupported claims**:
   - "first work" → "one of the first works"
   - "significantly better" → "better (15% improvement)"
   - "always" → "often" or "in most cases"

4. **Remove unsupported claims**:
   - If evidence cannot be found or generated
   - Delete the claim entirely

5. **Re-run check**:
   - After revisions, run check again
   - Iterate until alignment ≥ 80%

---

## Quality Gate

**Minimum threshold**: 80% of claims must be Supported or Weak (not Unsupported).

**Rationale**: 
- 100% is unrealistic (some claims are self-evident)
- < 80% indicates insufficient evidence
- Weak support is acceptable if claim is not central

---

## Common Patterns

### Pattern 1: Novelty Claims Without Evidence

❌ **Bad**:
> This is the first work to apply deep learning to single-cell batch correction.

✅ **Good**:
> To our knowledge, this is the first work to apply deep learning to single-cell batch correction (see Related Work for comparison with existing methods).

Or weaken:
> This is one of the first works to apply deep learning to single-cell batch correction.

---

### Pattern 2: Performance Claims Without Numbers

❌ **Bad**:
> Our method performs significantly better than existing approaches.

✅ **Good**:
> Our method achieves 0.92 ARI, outperforming Harmony (0.85 ARI) and Seurat CCA (0.82 ARI) by 8% and 12% respectively (Table 3).

---

### Pattern 3: Factual Claims Without Citations

❌ **Bad**:
> Batch effects are a major challenge in single-cell data integration.

✅ **Good**:
> Batch effects are a major challenge in single-cell data integration [@tran2020; @luecken2022].

---

### Pattern 4: Vague Efficiency Claims

❌ **Bad**:
> The approach is computationally efficient.

✅ **Good**:
> The approach processes 100,000 cells in 5 minutes on a standard laptop (Figure 2), 2x faster than Harmony.

---

## Self-Check Workflow

1. **After completing draft**: Run this check
2. **Generate report**: Use the format above
3. **If < 80% alignment**: Revise claims
4. **Re-check**: Iterate until passing
5. **Hand off**: Include the alignment report alongside the draft

---

## Example: Full Check

**Input**: Draft paper with Abstract and Introduction

**Output**:

```markdown
# Claim-Evidence Alignment Report

## Abstract Claims (5 total)

| # | Claim | Type | Evidence | Status |
|---|-------|------|----------|--------|
| 1 | "scRNA-seq has transformed cellular biology" | Factual | [@tang2009; @macosko2015] | ✅ Supported |
| 2 | "Batch effects remain a major challenge" | Factual | [@tran2020] | ✅ Supported |
| 3 | "We present AdaptiveHarmony" | Methodological | Methods 3.1 | ✅ Supported |
| 4 | "Achieves 0.90 ARI" | Performance | Table 3 | ✅ Supported |
| 5 | "Preserves 95% of rare cell markers" | Performance | Figure 4 | ✅ Supported |

## Introduction Claims (7 total)

| # | Claim | Type | Evidence | Status |
|---|-------|------|----------|--------|
| 6 | "Integration is essential" | Factual | [@luecken2022] | ✅ Supported |
| 7 | "Existing methods face trade-off" | Factual | [@korsunsky2019; @stuart2019] | ✅ Supported |
| 8 | "Rare cells are overcorrected" | Factual | No citation | ❌ Unsupported |
| 9 | "Different cell types need different correction" | Methodological | Methods 3.2 | ✅ Supported |
| 10 | "Outperforms Harmony by 8%" | Comparative | Table 3 | ✅ Supported |
| 11 | "Outperforms Seurat CCA by 12%" | Comparative | Table 3 | ✅ Supported |
| 12 | "Enables accurate rare cell identification" | Significance | Figure 5 case study | ✅ Supported |

## Summary
- Total: 12 claims
- Supported: 11 (92%)
- Unsupported: 1 (8%)
- **Alignment: 92% ✅ PASS**

## Action Items
1. Add citation for claim #8 or add experimental evidence in Results 4.3
```

---

## Tips for High Alignment

1. **Write claims with evidence in mind**: As you write, think "where is the evidence?"
2. **Add citations as you write**: Don't leave them for later
3. **Reference figures/tables explicitly**: "as shown in Figure 3"
4. **Be specific**: Concrete claims are easier to support
5. **Avoid superlatives**: "best", "first", "only" are hard to prove

---

## Citation Grounding (citation-side audit)

The Step 1-5 protocol above checks the **claim side** ("does this claim have any supporting evidence?"). The Citation Grounding audit checks the **citation side** ("does this citation actually support the claim it is attached to?"). Run after Step 4, before final hand-off.

### Grounding strength levels

| Level | Definition | Example | Action |
|-------|------------|---------|--------|
| **Strong** | Citation directly supports claim with experimental evidence | "Method X achieves 95% accuracy [1]" where [1] reports 95% in Table 2 | Keep |
| **Partial** | Citation supports claim but with caveats or limited scope | "Method X outperforms baselines [1]" where [1] only tested 3 baselines | Narrow wording ("outperforms three baselines") |
| **Weak** | Citation tangentially related but doesn't directly support claim | "Method X is widely used [1]" where [1] mentions X once in passing | Add stronger evidence or remove claim |
| **Unsupported** | Citation does not support claim at all (misattribution) | "Method X is the best [1]" where [1] doesn't compare methods | Remove or downgrade claim |

### Process

1. Extract major claims from Abstract / Introduction / Conclusion (don't audit every citation — focus on bold claims).
2. Read the cited papers (abstract, results, discussion).
3. Assess grounding strength for each (claim, citation) pair.
4. Suggest concrete action: keep / narrow / add / remove.

### Output (extends the claim-evidence report)

```markdown
## Citation Grounding Report

### Claim 1: "Our method achieves 95% accuracy on benchmark X"
- Citations: [12], [15]
- Grounding: Strong
- Evidence:
  - [12] Table 2 reports 95.2% accuracy on benchmark X
  - [15] Figure 3 shows consistent performance across datasets
- Action: Keep as-is

### Claim 2: "This is the first unsupervised approach"
- Citations: [8], [9]
- Grounding: Weak
- Action: Narrow to "first unsupervised approach for domain X" or add survey citation
```

### Common citation issues

- **Overclaiming**: "Our method is the best" but only compared 3 baselines → "outperforms three widely-used baselines".
- **Misattribution**: citing a review for a specific experimental result → cite the original paper.
- **Missing context**: "achieves 95% accuracy" without dataset → "95% on ImageNet [1]".
- **Circular citation**: citing your own paper for a claim that paper also doesn't support → find external validation or remove.

### Sentence-level reranking and attribution

When multiple candidate sources compete, when a draft answer lacks citations, or before a final citation check, rerank and attribute at the sentence level.

| Draft sentence | Source ID | Passage locator | Attribution strength | Risk |
|---|---|---|---|---|

Rules:

- Sentence-level scientific claims need source-level attribution.
- If the best source only supports part of the sentence, split or narrow the sentence to match what the source actually says.
- Do not keep unattributed conclusion sentences in high-stakes drafts (papers, grants, rebuttals).

### Constraints

- Focus on major claims (Abstract, Introduction, Conclusion); don't verify every reference.
- Partial grounding is acceptable if the claim is appropriately hedged.
- Better to weaken a claim than to overclaim.

Source for citation-grounding section: nature-citation, OpenScholar
open_scholar.py, PaperQA tools.py.

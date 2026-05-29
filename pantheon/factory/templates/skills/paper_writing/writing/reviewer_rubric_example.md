---
id: reviewer_rubric_example
name: "Peer Review Rubric — Worked Example"
description: |
  Worked example of a 3-reviewer NeurIPS-style peer review simulation plus a
  meta-review, illustrating how to apply [reviewer_rubric.md](./reviewer_rubric.md).
  Read this only when learning the rubric; skip when actively running a
  simulation.
source: https://github.com/SakanaAI/AI-Scientist
license: MIT
tags: [review, peer-review, example, neurips]
---

# Peer Review Rubric — Worked Example

This is a worked example of [reviewer_rubric.md](./reviewer_rubric.md) applied to a fictional manuscript ("AdaptiveHarmony" — a single-cell batch correction method). Use it to calibrate scoring; you do not need to read it during an actual peer review run.

## Reviewer 1 (Methodology Expert)

**Summary**: This paper presents AdaptiveHarmony, a batch correction method for single-cell RNA-seq that applies cell-type-specific correction strengths. The method is evaluated on 10 benchmark datasets and shows improved rare cell type preservation compared to existing methods.

**Strengths**:
- Technically sound approach with clear motivation
- Comprehensive experimental validation on 10 datasets
- Rigorous comparison with multiple baselines
- Reproducibility is excellent with detailed parameter settings
- Ablation study demonstrates importance of key components

**Weaknesses**:
- Sample size justification is missing for benchmark datasets
- Statistical significance testing is not reported for all comparisons
- Computational complexity analysis is limited
- Some experimental details are unclear (e.g., how many random seeds?)

**Questions**:
1. How does the method perform on datasets with very strong batch effects?
2. What is the computational complexity (time and space)?
3. How sensitive is the method to the choice of k (number of neighbors)?
4. Were multiple random seeds used for all experiments?

**Limitations**:
- Method assumes cell types are separable before correction
- Scalability to >1M cells is unclear
- Only evaluated on scRNA-seq, not other modalities

**Scores**:
- Originality: 3/4 (Good - novel approach to known problem)
- Quality: 3/4 (Good - solid experiments, minor gaps)
- Clarity: 3/4 (Good - generally clear)
- Significance: 3/4 (Good - addresses important problem)
- Soundness: 3/4 (Good - technically sound with minor issues)
- Presentation: 3/4 (Good - well-presented)
- Contribution: 3/4 (Good - solid contribution)
- Overall: 7/10 (Accept)
- Confidence: 4/5 (Quite confident)
- Decision: **Accept** (with minor revisions)

---

## Reviewer 2 (Novelty Expert)

**Summary**: The paper introduces cell-type-specific batch correction for preserving rare cell types. While the problem is important, the novelty is moderate as the core idea of adaptive correction has been explored before.

**Strengths**:
- Addresses an important problem (rare cell type preservation)
- Clear improvement over baselines (15-20% better marker preservation)
- Unsupervised approach is more practical than supervised alternatives
- Comprehensive comparison with existing methods

**Weaknesses**:
- Limited novelty - adaptive correction is not new
- Comparison with RareCorrect (most relevant prior work) is superficial
- No comparison with recent deep learning methods (e.g., scVI variants)
- Contribution is primarily empirical, limited theoretical insight

**Questions**:
1. How does this differ fundamentally from adaptive integration methods?
2. Why not compare with scVI-based methods more thoroughly?
3. What is the theoretical justification for cell-type-specific correction?

**Limitations**:
- Novelty is incremental
- Limited to scRNA-seq
- No theoretical analysis

**Scores**:
- Originality: 2/4 (Fair - incremental novelty)
- Quality: 3/4 (Good - solid experiments)
- Clarity: 3/4 (Good - clear presentation)
- Significance: 3/4 (Good - important problem)
- Soundness: 3/4 (Good - technically sound)
- Presentation: 3/4 (Good - well-written)
- Contribution: 2/4 (Fair - incremental)
- Overall: 6/10 (Borderline Accept)
- Confidence: 4/5 (Quite confident)
- Decision: **Borderline Accept** (needs stronger novelty justification)

---

## Reviewer 3 (Clarity Expert)

**Summary**: This paper is well-written and presents a clear solution to batch correction with rare cell type preservation. The presentation is strong, though some figures could be improved.

**Strengths**:
- Excellent writing quality and clarity
- Well-structured paper with logical flow
- Good use of figures to illustrate key points
- Accessible to non-experts
- Clear motivation and problem statement

**Weaknesses**:
- Figure 3 is too dense, hard to parse
- Some notation is inconsistent (e.g., θ vs theta)
- Abstract could be more concise
- Related work section is too brief

**Questions**:
1. Can Figure 3 be split into multiple figures?
2. Could you add a schematic diagram of the method?

**Limitations**:
- Some figures are dense
- Notation could be more consistent

**Scores**:
- Originality: 3/4 (Good)
- Quality: 3/4 (Good)
- Clarity: 4/4 (Excellent - very clear)
- Significance: 3/4 (Good)
- Soundness: 3/4 (Good)
- Presentation: 3/4 (Good - minor figure issues)
- Contribution: 3/4 (Good)
- Overall: 7/10 (Accept)
- Confidence: 3/5 (Moderately confident)
- Decision: **Accept** (with minor presentation improvements)

---

## Meta-Review

**Consensus**:
All reviewers agree that:
- The paper addresses an important problem
- Experimental validation is solid
- Writing is clear and accessible
- The method achieves good empirical results

**Disagreements**:
- Reviewer 2 thinks novelty is limited (2/4), while Reviewers 1 and 3 think it's good (3/4)
- Reviewer 2 is more critical of the contribution

**Critical Issues**:
None. All issues are minor and can be addressed in revision.

**Recommendation**: **Accept**

The paper makes a solid contribution to an important problem with good experimental validation and clear presentation. While novelty is moderate, the practical impact and empirical improvements justify acceptance.

**Required Revisions** (Minor):
1. Add statistical significance testing for all comparisons
2. Add sample size justification
3. Improve Figure 3 (split or simplify)
4. Add more thorough comparison with RareCorrect
5. Add computational complexity analysis
6. Clarify experimental details (random seeds, etc.)

**Overall Assessment**: 7/10 (Accept with minor revisions)

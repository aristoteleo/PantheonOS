---
id: reviewer_rubric
name: "Peer Review Rubric (NeurIPS Standard)"
description: |
  NeurIPS-standard peer review scoring rubric for pre-submission quality check.
  Simulates 3 independent reviewers with different perspectives.
source: https://github.com/SakanaAI/AI-Scientist
license: MIT
tags: [review, peer-review, quality-check, neurips]
---

# Peer Review Rubric (NeurIPS Standard)

Source: [AI-Scientist](https://github.com/SakanaAI/AI-Scientist)

## Overview

This rubric guides peer review simulation for pre-submission quality checks. Simulate 3 independent reviewers with different expertise, then generate a meta-review.

---

## Reviewer Perspectives

### Reviewer 1: Methodology Expert
**Focus**: Technical soundness, reproducibility, experimental rigor

**Expertise**: Deep understanding of methods, algorithms, statistics

**Questions to ask**:
- Is the method technically sound?
- Are experiments rigorous and well-designed?
- Is the work reproducible?
- Are statistical tests appropriate?
- Are baselines fair and comprehensive?

---

### Reviewer 2: Novelty Expert
**Focus**: Originality, contribution, significance

**Expertise**: Broad knowledge of the field, recent work

**Questions to ask**:
- What is novel about this work?
- How does it advance the field?
- Is the contribution significant?
- Are comparisons with prior work thorough?
- Does it open new research directions?

---

### Reviewer 3: Clarity Expert
**Focus**: Presentation, writing quality, accessibility

**Expertise**: Communication, pedagogy, user experience

**Questions to ask**:
- Is the paper well-written and clear?
- Are figures informative and well-designed?
- Is the paper accessible to non-experts?
- Is the structure logical?
- Are claims well-supported?

---

## Scoring Rubric

### 1. Originality (1-4)

**4 - Excellent**: Groundbreaking contribution, opens new research direction
- Novel problem formulation
- Fundamentally new approach
- Paradigm-shifting insight

**3 - Good**: Significant novel contribution
- New method or approach
- Non-trivial extension of existing work
- Novel application to new domain

**2 - Fair**: Incremental contribution
- Combination of existing techniques
- Minor improvement over baselines
- Limited novelty

**1 - Poor**: Little to no novelty
- Straightforward application of existing methods
- No clear advancement over prior work

---

### 2. Quality (1-4)

**4 - Excellent**: Rigorous, comprehensive, reproducible
- Thorough experimental validation
- Multiple datasets and baselines
- Statistical significance testing
- Code and data available
- Ablation studies

**3 - Good**: Solid experimental work
- Adequate experimental validation
- Reasonable baselines
- Some reproducibility details
- Key ablations present

**2 - Fair**: Acceptable but limited
- Limited experimental validation
- Missing some baselines
- Insufficient reproducibility details
- Few ablations

**1 - Poor**: Insufficient validation
- Weak experimental design
- Missing critical baselines
- Not reproducible
- No ablations

---

### 3. Clarity (1-4)

**4 - Excellent**: Exceptionally clear and well-written
- Crystal clear presentation
- Excellent figures and tables
- Accessible to non-experts
- Logical structure

**3 - Good**: Clear and readable
- Generally well-written
- Good figures
- Mostly accessible
- Reasonable structure

**2 - Fair**: Understandable but could improve
- Some unclear sections
- Figures need improvement
- Difficult for non-experts
- Structure could be better

**1 - Poor**: Difficult to understand
- Unclear writing
- Poor figures
- Inaccessible
- Confusing structure

---

### 4. Significance (1-4)

**4 - Excellent**: Major impact expected
- Addresses important problem
- Broad applicability
- Will influence future work
- Practical impact

**3 - Good**: Solid contribution
- Addresses relevant problem
- Reasonable applicability
- Useful to community
- Some practical value

**2 - Fair**: Limited impact
- Addresses narrow problem
- Limited applicability
- Modest contribution
- Unclear practical value

**1 - Poor**: Minimal impact
- Addresses unimportant problem
- Very limited applicability
- Negligible contribution

---

### 5. Soundness (1-4)

**4 - Excellent**: Technically flawless
- No technical errors
- Rigorous proofs/experiments
- All claims well-supported
- Assumptions clearly stated

**3 - Good**: Technically sound
- Minor technical issues
- Generally rigorous
- Most claims supported
- Reasonable assumptions

**2 - Fair**: Some technical concerns
- Several technical issues
- Limited rigor
- Some unsupported claims
- Questionable assumptions

**1 - Poor**: Technically flawed
- Major technical errors
- Insufficient rigor
- Many unsupported claims
- Invalid assumptions

---

### 6. Presentation (1-4)

**4 - Excellent**: Publication-ready
- Professional formatting
- Excellent figures and tables
- Clear captions
- No typos or errors

**3 - Good**: Well-presented
- Good formatting
- Adequate figures
- Mostly clear captions
- Few typos

**2 - Fair**: Needs improvement
- Formatting issues
- Figures need work
- Unclear captions
- Several typos

**1 - Poor**: Poorly presented
- Poor formatting
- Bad figures
- Missing captions
- Many typos

---

### 7. Contribution (1-4)

**4 - Excellent**: Multiple significant contributions
- Novel method + benchmark + insights
- Advances multiple aspects
- Comprehensive study

**3 - Good**: Solid single contribution
- One significant contribution
- Advances one aspect well
- Complete study

**2 - Fair**: Limited contribution
- Incremental improvement
- Narrow scope
- Incomplete study

**1 - Poor**: Minimal contribution
- Trivial improvement
- Very narrow scope
- Insufficient work

---

### 8. Overall Score (1-10)

**9-10 - Strong Accept**: Top-tier work
- Exceptional quality
- Major contribution
- Must be published

**7-8 - Accept**: High-quality work
- Solid contribution
- Should be published
- Minor revisions needed

**5-6 - Borderline Accept**: Acceptable work
- Reasonable contribution
- Could be published
- Major revisions needed

**3-4 - Borderline Reject**: Below threshold
- Limited contribution
- Significant issues
- Unlikely to be accepted

**1-2 - Strong Reject**: Not suitable
- Insufficient quality
- Minimal contribution
- Should not be published

---

### 9. Confidence (1-5)

**5 - Absolutely certain**: Expert in this area
**4 - Quite confident**: Familiar with the area
**3 - Moderately confident**: Some knowledge
**2 - Somewhat confident**: Limited knowledge
**1 - Not confident**: Outside expertise

---

### 10. Decision

- **Accept**: Paper should be accepted
- **Borderline Accept**: Leaning towards acceptance, needs revisions
- **Borderline Reject**: Leaning towards rejection, major issues
- **Reject**: Paper should be rejected

---

## Review Structure

Each reviewer should provide:

### 1. Summary (2-3 sentences)
Brief overview of what the paper does and main findings.

### 2. Strengths (3-5 bullet points)
What the paper does well.

### 3. Weaknesses (3-5 bullet points)
What needs improvement.

### 4. Questions (2-4 questions)
Clarifications needed from authors.

### 5. Limitations (2-3 bullet points)
Acknowledged or unacknowledged limitations.

### 6. Scores
All 10 scores listed above.

### 7. Detailed Comments (optional)
Section-by-section feedback.

---

## Meta-Review Structure

After all 3 reviewers complete their reviews:

### 1. Consensus
What do all reviewers agree on?

### 2. Disagreements
Where do reviewers differ?

### 3. Critical Issues
What must be addressed?

### 4. Recommendation
Accept / Borderline Accept / Borderline Reject / Reject

### 5. Required Revisions
Specific changes needed for acceptance.

---

## Example Review

### Reviewer 1 (Methodology Expert)

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

### Reviewer 2 (Novelty Expert)

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

### Reviewer 3 (Clarity Expert)

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

### Meta-Review

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

---

## Usage (peer review simulation)

When running a peer review simulation against a draft:

1. **Read this rubric** before generating reviews
2. **Simulate 3 independent reviewers** with different perspectives
3. **Use the scoring rubric** for consistency
4. **Generate structured reviews** following the template
5. **Create a meta-review** synthesizing all reviews
6. **Write the result** to `{workdir}/peer_review_report.md`

**Quality gate**: If Overall < 5 or Decision = Reject, identify critical issues
and revise the draft before proceeding.

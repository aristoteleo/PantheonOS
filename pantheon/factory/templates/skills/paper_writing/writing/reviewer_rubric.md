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

## Worked Example

A full 3-reviewer + meta-review worked example (fictional "AdaptiveHarmony" manuscript) lives in [reviewer_rubric_example.md](./reviewer_rubric_example.md). Read it once when first calibrating to the rubric; you do not need to load it during an actual peer review run.

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

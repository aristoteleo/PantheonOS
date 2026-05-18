---
id: grant_proposal_scenario
name: "Grant Proposal Scenario"
description: |
  Workflow for packaging research ideas into aims, plans, and impact
  for grant applications.
source: https://github.com/assafelovic/academic-research-skills
license: Apache 2.0
---

# Grant Proposal Scenario

## When to Use

- User says: "基金申请", "grant proposal", "funding application", "NIH grant", "NSF proposal"
- Goal: Package research ideas into compelling grant proposal
- Output: Structured proposal with aims, approach, significance, and innovation

---

## Workflow Overview

```
Define Research Question → Structure Aims → Write Significance & Innovation 
  → Develop Research Plan → Budget & Timeline → Generate Proposal
```

---

## Detailed Steps

### Step 1: Define Research Question

**Leader action**: Clarify the central research question and hypothesis

**Questions to answer**:
1. What is the overarching research question?
2. What is the hypothesis?
3. What gap in knowledge does this address?
4. Why is this important now?

**Deliverable**: `{workdir}/research_question.md`

**Example**:
```markdown
# Research Question

**Central Question**: 
How can we preserve rare cell types during single-cell data integration?

**Hypothesis**: 
Cell-type-specific batch correction will preserve rare cell types better than uniform correction.

**Knowledge Gap**: 
Current batch correction methods apply uniform correction, causing rare cell types to be overcorrected and lost.

**Timeliness**: 
As single-cell atlases grow to millions of cells, preserving rare but biologically important populations is critical for understanding disease mechanisms.
```

---

### Step 2: Structure Specific Aims

**Leader action**: Define 2-3 specific aims that address the research question

**Aim structure**:
- **Aim 1**: Foundational (develop method/tool)
- **Aim 2**: Validation (test on real data)
- **Aim 3**: Application (biological discovery or clinical impact)

**Each aim should have**:
- Clear objective (what you will do)
- Rationale (why this is important)
- Approach (how you will do it)
- Expected outcome (what you will learn)

**Deliverable**: `{workdir}/specific_aims.md`

**Example**:
```markdown
# Specific Aims

## Aim 1: Develop AdaptiveHarmony for Cell-Type-Specific Batch Correction

**Objective**: Develop and validate a computational method that automatically estimates optimal batch correction strength for each cell type.

**Rationale**: Current methods apply uniform correction, causing rare cell types to be overcorrected. Cell-type-specific correction will preserve rare populations while removing batch effects.

**Approach**: 
1. Develop algorithm to estimate cell type structure from uncorrected data
2. Calculate optimal correction strength per cell type
3. Implement adaptive correction framework
4. Benchmark on 10 public datasets

**Expected Outcome**: A validated method that preserves 90%+ of rare cell type markers while achieving batch mixing comparable to existing methods.

**Timeline**: Months 1-12

---

## Aim 2: Apply AdaptiveHarmony to Human Cell Atlas Construction

**Objective**: Integrate 20 bone marrow datasets from the Human Cell Atlas to discover rare hematopoietic populations.

**Rationale**: Bone marrow contains rare progenitor populations critical for understanding blood disorders. Standard integration methods lose these populations.

**Approach**:
1. Integrate 20 HCA bone marrow datasets (500K cells total)
2. Identify rare populations (<1% frequency)
3. Validate with marker gene expression
4. Compare with standard integration methods

**Expected Outcome**: Discovery of 3-5 rare hematopoietic populations not detected by standard methods, with validated marker signatures.

**Timeline**: Months 13-24

---

## Aim 3: Validate Rare Populations with Experimental Follow-Up

**Objective**: Experimentally validate biological relevance of computationally discovered rare populations.

**Rationale**: Computational discovery must be validated experimentally to confirm biological significance.

**Approach**:
1. Select 2 rare populations from Aim 2
2. Design FACS sorting strategy based on marker genes
3. Isolate populations and perform functional assays
4. Validate differentiation potential and disease relevance

**Expected Outcome**: Experimental confirmation that computationally discovered rare populations have distinct functional properties and disease relevance.

**Timeline**: Months 25-36

**Collaborator**: Dr. Jane Smith (FACS expert, commitment letter attached)
```

---

### Step 3: Write Significance and Innovation

**Leader calls**: `writer`

**Instruction**:
```
Write Significance and Innovation sections. Workdir: {workdir}.
Research question: {workdir}/research_question.md
Specific aims: {workdir}/specific_aims.md

Significance section should address:
- Importance of the problem
- Impact on the field
- Clinical/translational relevance
- Broader impact

Innovation section should address:
- Novel concepts or approaches
- Advantages over existing methods
- Paradigm-shifting potential
- New tools/resources for community

Deliverable: {workdir}/draft/significance_innovation.md
```

**Significance structure**:
1. **Problem importance** (1 paragraph)
2. **Current limitations** (1 paragraph)
3. **How this work addresses limitations** (1 paragraph)
4. **Expected impact** (1 paragraph)

**Innovation structure**:
1. **Novel concepts** (1 paragraph)
2. **Technical innovations** (1 paragraph)
3. **Advantages over existing approaches** (1 paragraph)

**Deliverable**: `{workdir}/draft/significance_innovation.md`

**Example**:
```markdown
## Significance

Single-cell RNA sequencing has revolutionized our understanding of cellular heterogeneity, enabling the profiling of thousands of individual cells. As the field moves toward constructing comprehensive cell atlases, integrating data from multiple experiments has become essential. However, batch effects—systematic technical variations between experiments—obscure biological signals and must be corrected. Current batch correction methods face a critical limitation: they apply uniform correction across all cell types, causing rare populations to be overcorrected and lost. This is particularly problematic because rare cell types, though comprising <1% of cells, are often biologically critical for understanding disease mechanisms and therapeutic targets.

This proposal addresses this limitation by developing AdaptiveHarmony, a method that applies cell-type-specific correction strengths. By preserving rare cell types during integration, this work will enable more accurate atlas construction and facilitate the discovery of novel cell populations. The expected impact spans three areas: (1) methodological—providing the community with a validated tool for rare cell preservation, (2) biological—discovering rare hematopoietic populations in the Human Cell Atlas, and (3) clinical—identifying potential therapeutic targets in rare progenitor populations implicated in blood disorders.

## Innovation

This proposal introduces several conceptual and technical innovations. **Conceptually**, we challenge the assumption that all cell types require the same correction strength, proposing instead that correction should be adaptive based on cell type abundance and structure. This represents a paradigm shift from uniform to personalized correction. **Technically**, we develop an unsupervised algorithm that automatically estimates optimal correction strength per cell type without requiring cell type labels, making it applicable to datasets where rare populations are unknown a priori. **Methodologically**, we combine computational discovery with experimental validation, ensuring that computationally identified rare populations are biologically meaningful.

Compared to existing approaches, AdaptiveHarmony offers three key advantages: (1) it preserves rare cell types that uniform methods lose, (2) it operates in an unsupervised manner without requiring labels, and (3) it achieves comparable computational efficiency to existing methods. The method will be released as open-source software with comprehensive documentation, providing the community with a validated tool for rare cell preservation. The experimental validation framework (Aim 3) establishes a generalizable approach for confirming computational discoveries, advancing the integration of computational and experimental methods in single-cell biology.
```

---

### Step 4: Develop Research Plan

**Leader calls**: `writer`

**Instruction**:
```
Write detailed research plan for each aim. Workdir: {workdir}.
Specific aims: {workdir}/specific_aims.md

For each aim, include:
- Background and rationale (1-2 paragraphs)
- Preliminary data (if available)
- Experimental design (detailed)
- Expected results
- Potential pitfalls and alternative approaches
- Timeline

Deliverable: {workdir}/draft/research_plan.md
```

**Research plan structure for each aim**:

```markdown
### Aim 1: [Title]

#### Background and Rationale
[1-2 paragraphs explaining why this aim is important and feasible]

#### Preliminary Data
[If available, show proof-of-concept results]

#### Experimental Design

**Task 1.1: [Subtask title]**
- **Approach**: [Detailed methodology]
- **Data**: [What data will be used]
- **Analysis**: [How data will be analyzed]
- **Success criteria**: [How to know if it worked]

**Task 1.2: [Subtask title]**
[Same structure]

#### Expected Results
[What you expect to find and why]

#### Potential Pitfalls and Alternative Approaches

**Pitfall 1**: [What could go wrong]
- **Alternative**: [Backup plan]

**Pitfall 2**: [What could go wrong]
- **Alternative**: [Backup plan]

#### Timeline
- Months 1-3: Task 1.1
- Months 4-6: Task 1.2
- Months 7-9: Task 1.3
- Months 10-12: Analysis and manuscript preparation
```

**Deliverable**: `{workdir}/draft/research_plan.md`

---

### Step 5: Budget and Timeline

**Leader action**: Create budget justification and project timeline

**Budget categories** (example for NIH R01):
- **Personnel**: PI, postdoc, graduate student
- **Equipment**: Computational resources, lab equipment
- **Supplies**: Reagents, consumables
- **Travel**: Conferences, collaborations
- **Other**: Publication costs, software licenses

**Timeline format**:
- Gantt chart showing aims and tasks over project period
- Milestones and decision points
- Dependencies between aims

**Deliverable**: 
- `{workdir}/budget.md`
- `{workdir}/timeline.md`

---

### Step 6: Generate Proposal Document

**Leader calls**: `reporter`

**Instruction**:
```
Generate grant proposal document. Workdir: {workdir}.

Combine:
- Specific aims: {workdir}/specific_aims.md
- Significance & Innovation: {workdir}/draft/significance_innovation.md
- Research plan: {workdir}/draft/research_plan.md
- Budget: {workdir}/budget.md
- Timeline: {workdir}/timeline.md

Format according to funding agency requirements (NIH, NSF, etc.)
Theme: academic_minimal
Output: {workdir}/report/proposal.html + proposal.pdf
```

**Deliverable**:
- `{workdir}/report/proposal.html`
- `{workdir}/report/proposal.pdf`

---

## Output Structure

```
{workdir}/
├── research_question.md         # Central question and hypothesis
├── specific_aims.md             # 2-3 specific aims
├── draft/
│   ├── significance_innovation.md
│   └── research_plan.md
├── budget.md
├── timeline.md
└── report/
    ├── proposal.html
    └── proposal.pdf
```

---

## Grant Proposal Structure (NIH R01 Example)

```markdown
# Grant Proposal: AdaptiveHarmony for Rare Cell Type Preservation

## Specific Aims (1 page)

[2-3 aims, each with objective, rationale, approach, expected outcome]

## Research Strategy (12 pages)

### A. Significance (2 pages)
- Problem importance
- Current limitations
- How this work addresses limitations
- Expected impact

### B. Innovation (1 page)
- Novel concepts
- Technical innovations
- Advantages over existing approaches

### C. Approach (9 pages)

#### Aim 1: [Title] (3 pages)
- Background and rationale
- Preliminary data
- Experimental design
- Expected results
- Potential pitfalls and alternatives
- Timeline

#### Aim 2: [Title] (3 pages)
[Same structure]

#### Aim 3: [Title] (3 pages)
[Same structure]

## Bibliography (no page limit)

## Budget and Justification (5 pages)
```

---

## Writing Guidelines

### Use Active Voice and Strong Verbs

❌ **Bad**: "The method will be developed."
✅ **Good**: "We will develop the method."

### Be Specific About Outcomes

❌ **Bad**: "We will improve batch correction."
✅ **Good**: "We will achieve 90%+ rare cell marker preservation while maintaining batch mixing ARI >0.85."

### Show Feasibility

❌ **Bad**: "We will develop a novel algorithm."
✅ **Good**: "Building on our preliminary algorithm (Figure 1), we will develop..."

### Address Reviewers' Concerns Proactively

Include "Potential Pitfalls and Alternatives" for every aim.

---

## Quality Checklist

Before finalizing proposal:

- [ ] **Specific aims are clear and achievable**
- [ ] **Significance is compelling** (why this matters)
- [ ] **Innovation is explicit** (what's new)
- [ ] **Feasibility is demonstrated** (preliminary data)
- [ ] **Pitfalls are addressed** (backup plans)
- [ ] **Timeline is realistic** (not overly ambitious)
- [ ] **Budget is justified** (every line item explained)
- [ ] **Page limits are met** (NIH: 12 pages for Research Strategy)

---

## Common Mistakes to Avoid

### 1. Vague Aims

❌ **Bad**: "Aim 1: Improve batch correction"
✅ **Good**: "Aim 1: Develop AdaptiveHarmony to preserve 90%+ of rare cell markers"

### 2. No Preliminary Data

❌ **Bad**: "We will develop a novel method."
✅ **Good**: "Building on our pilot study (Figure 1), we will..."

### 3. Overly Ambitious

❌ **Bad**: 5 aims in 3 years
✅ **Good**: 3 aims in 3 years with realistic timelines

### 4. No Pitfalls Section

❌ **Bad**: (No mention of what could go wrong)
✅ **Good**: "Pitfall: Method may not converge. Alternative: Use different initialization."

### 5. Weak Significance

❌ **Bad**: "This is interesting."
✅ **Good**: "This addresses a critical barrier to atlas construction, impacting 1000+ researchers."

---

## Success Metrics

A successful grant proposal:
- Clear, achievable specific aims
- Compelling significance and innovation
- Demonstrated feasibility with preliminary data
- Realistic timeline and budget
- Proactive addressing of potential pitfalls
- Meets all formatting requirements
- Competitive for funding

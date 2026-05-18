---
id: revision_response_scenario
name: "Revision Response Scenario"
description: |
  Workflow for converting reviewer comments into point-by-point responses
  and revised manuscript.
source: https://github.com/assafelovic/academic-research-skills
license: Apache 2.0
---

# Revision Response Scenario

## When to Use

- User says: "审稿返修", "reviewer comments", "revision", "rebuttal"
- User provides: reviewer_comments.txt or PDF with comments
- Goal: Produce revised paper + point-by-point response letter

---

## Workflow Overview

```
Parse Comments → Classify & Prioritize → Revision Roadmap 
  → Revise Paper → Generate Response Letter → Render Outputs
```

---

## Detailed Steps

### Step 1: Parse Reviewer Comments

**Leader action**: Parse reviewer comments from user input

**Input formats supported**:
- Plain text file (reviewer_comments.txt)
- PDF with annotations
- Structured JSON

**Parsing protocol**:
1. Identify reviewers (Reviewer 1, Reviewer 2, etc.)
2. Extract individual comments
3. Number each comment (R1-C1, R1-C2, R2-C1, etc.)
4. Preserve original text

**Deliverable**: `{workdir}/parsed_comments.json`

```json
{
  "reviewer_1": [
    {"id": "R1-C1", "text": "Sample size justification is missing", "section": "Methods"},
    {"id": "R1-C2", "text": "Figure 3 is too dense", "section": "Results"}
  ],
  "reviewer_2": [
    {"id": "R2-C1", "text": "Novelty is limited", "section": "Introduction"}
  ]
}
```

---

### Step 2: Classify and Prioritize Comments

**Leader action**: Classify each comment by severity and type

**Severity levels**:
- **Critical**: Must fix for acceptance (e.g., "major technical flaw")
- **Important**: Should fix (e.g., "missing baseline comparison")
- **Nice-to-have**: Can fix (e.g., "typo in caption")

**Comment types**:
- **Methodology**: Technical issues, experimental design
- **Novelty**: Contribution, comparison with prior work
- **Clarity**: Writing, figures, presentation
- **Ethical**: Data availability, reproducibility
- **Editorial**: Typos, formatting

**Actionability**:
- **Actionable**: Can be directly addressed
- **Needs clarification**: Requires understanding reviewer intent
- **Cannot address**: Outside scope or infeasible

**Deliverable**: `{workdir}/classified_comments.json`

```json
{
  "R1-C1": {
    "text": "Sample size justification is missing",
    "severity": "Critical",
    "type": "Methodology",
    "actionability": "Actionable",
    "section": "Methods"
  }
}
```

---

### Step 3: Generate Revision Roadmap

**Leader action**: Create prioritized revision plan

**Roadmap structure**:
1. **Critical issues** (must fix)
2. **Important issues** (should fix)
3. **Nice-to-have** (can fix)

For each issue:
- Comment ID and text
- Current state
- Proposed action
- Evidence/changes needed
- Estimated effort
- Status (not started / in progress / completed)

**Deliverable**: `{workdir}/revision_roadmap.md`

**Example**:
```markdown
# Revision Roadmap

## Critical Issues (Must Fix)

### R1-C1: Sample size justification missing
- **Reviewer**: Reviewer 1
- **Section**: Methods
- **Current State**: No power analysis or sample size calculation
- **Action**: Add Section 3.1 "Sample Size Calculation"
- **Evidence**: Cite Cohen (1988) for effect size estimation
- **Estimated Effort**: 2 hours
- **Status**: 🔴 Not started

### R2-C1: Comparison with baseline X needed
- **Reviewer**: Reviewer 2
- **Section**: Results
- **Current State**: Only compared with baseline Y
- **Action**: Add Table 3 comparing with baseline X
- **Evidence**: Run experiments and add results
- **Estimated Effort**: 4 hours
- **Status**: 🔴 Not started

## Important Issues (Should Fix)

### R1-C2: Figure 3 caption unclear
- **Reviewer**: Reviewer 1
- **Section**: Results
- **Current State**: Caption is too brief
- **Action**: Expand caption to describe experimental conditions
- **Estimated Effort**: 15 minutes
- **Status**: 🔴 Not started

## Summary
- Total comments: 15
- Critical: 3 (must fix)
- Important: 7 (should fix)
- Nice-to-have: 5 (can fix)
- Estimated total effort: 2-3 days
```

---

### Step 4: Revise Paper

**Leader calls**: `writer`

**Instruction**:
```
Revise the paper based on revision roadmap. Workdir: {workdir}.
Source: {workdir}/draft/paper.md
Roadmap: {workdir}/revision_roadmap.md

For each issue in the roadmap:
1. Read the comment and proposed action
2. Make the necessary changes to paper.md
3. Track changes (optional: use diff or track changes mode)
4. Update roadmap status to "completed"

Prioritize Critical issues first, then Important, then Nice-to-have.

When revising sections, continue to follow writing best practices:
- Read corresponding skill files (abstract.md, introduction.md, etc.)
- Maintain quality standards
- Ensure claim-evidence alignment

Deliverable: {workdir}/draft/paper_revised.md
```

**Deliverable**: `{workdir}/draft/paper_revised.md`

---

### Step 5: Generate Response Letter

**Leader calls**: `writer` (or new `response_letter_writer` agent)

**Instruction**:
```
Generate point-by-point response letter. Workdir: {workdir}.
Parsed comments: {workdir}/parsed_comments.json
Revision roadmap: {workdir}/revision_roadmap.md
Revised paper: {workdir}/draft/paper_revised.md

For each comment, generate a response following this structure:

**Comment**: [Quote original comment]
**Response**: [Acknowledgment + Explanation + Action + Evidence]
**Changes Made**: [Specific location in revised manuscript]

Use respectful, professional tone:
- Acknowledge the reviewer's concern
- Explain your understanding
- Describe what you did
- Point to evidence in revised manuscript

Deliverable: {workdir}/response_letter.md
```

**Response format for each comment**:

```markdown
### R1-C1: Sample size justification missing

**Comment**: 
> "The authors do not provide justification for the sample size used in their experiments."

**Response**: 
We thank the reviewer for this important observation. We have now added a sample size justification in Section 3.1 (page 8, lines 234-245). We performed a power analysis using G*Power 3.1, assuming a medium effect size (Cohen's d = 0.5), α = 0.05, and power = 0.80. This analysis indicated that a minimum sample size of n = 64 per group is required. Our study includes n = 100 per group, providing adequate statistical power.

**Changes Made**: 
- Added Section 3.1 "Sample Size Calculation" (page 8, lines 234-245)
- Added citation: Cohen, J. (1988). Statistical Power Analysis for the Behavioral Sciences.
```

**Deliverable**: `{workdir}/response_letter.md`

---

### Step 6: Render Outputs

**Leader calls**: `reporter`

**Instruction**:
```
Generate outputs for revision submission. Workdir: {workdir}.

1. Revised paper HTML preview:
   Source: {workdir}/draft/paper_revised.md
   Output: {workdir}/report/revised_preview.html
   Theme: academic_latex

2. Response letter HTML:
   Source: {workdir}/response_letter.md
   Output: {workdir}/report/response_letter.html
   Theme: revision_response (if available, else academic_minimal)

3. Optional: Track changes PDF showing differences
   Source: {workdir}/draft/paper.md vs paper_revised.md
   Output: {workdir}/report/changes_tracked.pdf
```

**Deliverable**:
- `{workdir}/report/revised_preview.html`
- `{workdir}/report/response_letter.html`
- `{workdir}/report/changes_tracked.pdf` (optional)

---

## Output Structure

```
{workdir}/
├── reviewer_comments.txt           # User input
├── parsed_comments.json            # Parsed comments
├── classified_comments.json        # Classified comments
├── revision_roadmap.md             # Prioritized plan
├── draft/
│   ├── paper.md                    # Original
│   └── paper_revised.md            # Revised
├── response_letter.md              # Point-by-point response
└── report/
    ├── revised_preview.html
    ├── response_letter.html
    └── changes_tracked.pdf         # Optional
```

---

## Response Letter Structure

```markdown
# Response to Reviewers

Dear Editor and Reviewers,

We thank the editor and reviewers for their thoughtful comments and suggestions. We have carefully addressed all comments and believe the manuscript has been significantly improved. Below, we provide point-by-point responses to each comment.

## Reviewer 1

### R1-C1: [Comment title]
**Comment**: [Quote]
**Response**: [Response]
**Changes Made**: [Location]

### R1-C2: [Comment title]
**Comment**: [Quote]
**Response**: [Response]
**Changes Made**: [Location]

## Reviewer 2

[Same structure]

## Summary of Major Changes

1. Added sample size justification (Section 3.1)
2. Added comparison with baseline X (Table 3)
3. Improved Figure 3 caption
4. [Other major changes]

We hope that these revisions adequately address the reviewers' concerns and that the manuscript is now suitable for publication.

Sincerely,
[Authors]
```

---

## Response Tone Guidelines

### Acknowledge Concerns
✅ **Good**: "We thank the reviewer for this important observation."
❌ **Bad**: "The reviewer is mistaken."

### Explain Clearly
✅ **Good**: "We have now added a sample size justification in Section 3.1."
❌ **Bad**: "We already mentioned this."

### Be Specific
✅ **Good**: "See page 8, lines 234-245."
❌ **Bad**: "See Methods section."

### Stay Professional
✅ **Good**: "We respectfully disagree with this assessment because..."
❌ **Bad**: "This comment is unfair."

---

## Handling Difficult Comments

### Comment: "Novelty is limited"

**Response strategy**:
1. Acknowledge the concern
2. Clarify the contribution
3. Emphasize practical impact
4. Add comparison with recent work

**Example**:
> We appreciate the reviewer's concern about novelty. While the core idea of adaptive correction has been explored in prior work, our contribution is the first to apply cell-type-specific correction strengths in an unsupervised manner. We have now expanded the Related Work section (page 4, lines 123-145) to more clearly distinguish our approach from prior work. Additionally, we emphasize that our method achieves 15-20% better rare cell type preservation than existing methods, demonstrating significant practical impact.

---

### Comment: "Cannot address" (e.g., "Add 10 more datasets")

**Response strategy**:
1. Acknowledge the value of the suggestion
2. Explain constraints (time, resources, scope)
3. Offer alternative evidence
4. Commit to future work

**Example**:
> We agree that additional datasets would strengthen the evaluation. However, given the scope and timeline of this revision, we have focused on improving the analysis of existing datasets. We have now added more detailed ablation studies (Figure 5) and statistical significance testing (Table 3) to strengthen the conclusions. We plan to evaluate the method on additional datasets in future work.

---

## Quality Checklist

Before finalizing revision:

- [ ] **All Critical issues addressed**
- [ ] **All Important issues addressed** (or explained why not)
- [ ] **Response letter complete** (every comment has a response)
- [ ] **Changes are specific** (page/line numbers provided)
- [ ] **Tone is professional** (no defensive language)
- [ ] **Revised paper maintains quality** (claim-evidence alignment, etc.)
- [ ] **Summary of changes included**

---

## Success Metrics

A successful revision response produces:
- Revised paper addressing all critical issues
- Complete point-by-point response letter
- Professional, respectful tone throughout
- Specific evidence for all changes
- Maintained or improved paper quality

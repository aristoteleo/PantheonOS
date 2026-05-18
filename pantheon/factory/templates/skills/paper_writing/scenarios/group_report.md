---
id: group_report_scenario
name: "Group Report Scenario"
description: |
  Workflow for turning scattered research progress into a clear,
  structured research story for lab meetings or progress reports.
source: https://github.com/assafelovic/academic-research-skills
license: Apache 2.0
---

# Group Report Scenario

## When to Use

- User says: "组会汇报", "lab meeting", "progress report", "weekly report"
- Goal: Organize scattered progress into clear research narrative
- Output: Structured report (HTML/PDF) suitable for presentation

---

## Workflow Overview

```
Gather Materials → Structure Progress → Write Narrative 
  → Identify Blockers → Next Steps → Render Report
```

---

## Detailed Steps

### Step 1: Gather Materials

**Leader calls**: `researcher`

**Instruction**:
```
Organize research materials for progress report. Workdir: {workdir}.
Source materials: {absolute path list}.

Classify materials:
- Experimental results (data, figures, tables)
- Code/analysis scripts
- Literature notes
- Meeting notes
- Blockers/issues

Create inventory: {workdir}/materials/inventory.md
```

**Deliverable**: `{workdir}/materials/inventory.md`

---

### Step 2: Structure Progress

**Leader action**: Define report structure based on progress type

**Structure options**:

**Option A: Experiment-Focused**
1. **Background**: What we're investigating
2. **This Week's Work**: What experiments were run
3. **Key Findings**: What we learned
4. **Blockers**: What's stuck
5. **Next Steps**: What's next

**Option B: Milestone-Focused**
1. **Project Goal**: Overall objective
2. **Progress Towards Milestone**: What's done vs. planned
3. **Key Results**: Main achievements
4. **Challenges**: What's difficult
5. **Timeline Update**: Revised schedule

**Option C: Multi-Project**
1. **Project A Progress**
2. **Project B Progress**
3. **Cross-Project Insights**
4. **Resource Needs**
5. **Priorities for Next Period**

**Leader decision**: Choose structure based on user input and materials.

---

### Step 3: Write Narrative

**Leader calls**: `writer`

**Instruction**:
```
Write progress report. Mode: {bio|generic}. Structure: {chosen structure}.
Materials: {workdir}/materials/inventory.md

Writing guidelines:
- Use clear, concise language (not formal paper style)
- Lead with findings, not methods
- Include figures/tables inline
- Highlight blockers prominently
- Be honest about challenges

Deliverable: {workdir}/draft/report.md
```

**Writing style for group reports**:
- **Conversational**: More casual than paper writing
- **Visual**: Heavy use of figures and tables
- **Honest**: Acknowledge failures and challenges
- **Forward-looking**: Emphasize next steps

**Deliverable**: `{workdir}/draft/report.md`

---

### Step 4: Identify Blockers

**Leader action**: Extract and categorize blockers

**Blocker types**:
- **Technical**: Method not working, code bugs
- **Resource**: Need equipment, compute, data
- **Knowledge**: Don't know how to proceed
- **External**: Waiting on collaborators, reagents

**For each blocker**:
- Description
- Impact (high/medium/low)
- Possible solutions
- Help needed

**Deliverable**: `{workdir}/blockers.md`

**Example**:
```markdown
# Blockers

## High Impact

### Batch correction not converging on Dataset X
- **Type**: Technical
- **Impact**: Blocks main analysis
- **Tried**: Adjusted parameters, different methods
- **Possible solutions**: 
  1. Try different initialization
  2. Consult with method author
  3. Use alternative dataset
- **Help needed**: Advice from PI or expert

## Medium Impact

### Need GPU access for large-scale experiments
- **Type**: Resource
- **Impact**: Slows down experiments
- **Possible solutions**:
  1. Request cluster allocation
  2. Use cloud credits
- **Help needed**: Cluster access approval
```

---

### Step 5: Define Next Steps

**Leader action**: Generate concrete action items

**Next steps format**:
- **Task**: Specific, actionable item
- **Owner**: Who will do it
- **Deadline**: When it should be done
- **Dependencies**: What needs to happen first

**Deliverable**: `{workdir}/next_steps.md`

**Example**:
```markdown
# Next Steps

## This Week (by Friday)
- [ ] Run batch correction with new parameters (Alice)
- [ ] Analyze results from Experiment 3 (Bob)
- [ ] Draft Methods section (Alice)

## Next Week
- [ ] Request GPU cluster access (Alice)
- [ ] Meet with collaborator about Dataset Y (Bob)
- [ ] Prepare figures for manuscript (Alice)

## This Month
- [ ] Complete all experiments for Figure 3
- [ ] Draft Results section
- [ ] Submit preprint
```

---

### Step 6: Render Report

**Leader calls**: `reporter`

**Instruction**:
```
Generate progress report outputs. Workdir: {workdir}.
Source: {workdir}/draft/report.md
Theme: general_report (wide layout, card-based sections)
Slug: {slug}

Outputs:
1. HTML preview: {workdir}/report/{slug}_report.html
2. Optional: PDF for sharing
```

**Deliverable**:
- `{workdir}/report/{slug}_report.html`
- `{workdir}/report/{slug}_report.pdf` (optional)

---

## Report Structure Template

```markdown
# Research Progress Report
**Date**: {date}
**Project**: {project name}
**Researcher**: {name}

---

## Background

{1-2 paragraphs: What is the project about? What are we trying to achieve?}

---

## This Week's Work

### Experiments Completed
- Experiment 1: {brief description}
- Experiment 2: {brief description}

### Analysis Performed
- Analysis 1: {brief description}

### Code/Tools Developed
- Tool 1: {brief description}

---

## Key Findings

### Finding 1: {Title}
{Description with figure/table}

![Figure 1](path/to/figure.png)
**Figure 1**: {Caption}

**Interpretation**: {What does this mean?}

### Finding 2: {Title}
{Description}

---

## Challenges & Blockers

### Blocker 1: {Title}
- **Issue**: {Description}
- **Impact**: {High/Medium/Low}
- **Tried**: {What we attempted}
- **Help needed**: {What would help}

---

## Next Steps

### This Week
- [ ] Task 1
- [ ] Task 2

### Next Week
- [ ] Task 3
- [ ] Task 4

---

## Questions for Discussion

1. {Question 1}
2. {Question 2}
```

---

## Writing Guidelines

### Lead with Findings, Not Methods

❌ **Bad**: "We ran batch correction using Harmony with theta=2."
✅ **Good**: "Batch correction successfully integrated 10 datasets (Figure 1). We used Harmony with theta=2."

---

### Use Figures Liberally

Every key finding should have a figure or table.

**Example**:
> **Finding**: Rare cell types are preserved after correction.
> 
> ![Marker preservation](figures/marker_preservation.png)
> **Figure 2**: Marker gene preservation for rare cell types. AdaptiveHarmony (red) preserves 95% of markers vs. 78% for Harmony (blue).

---

### Be Honest About Failures

❌ **Bad**: (Don't mention failed experiments)
✅ **Good**: "Experiment 2 failed due to contamination. We're repeating with fresh reagents."

---

### Highlight Blockers Prominently

Use callout boxes or colored sections:

```markdown
> ⚠️ **BLOCKER**: Batch correction not converging on Dataset X. Need advice on parameter tuning.
```

---

### Include Concrete Next Steps

❌ **Bad**: "Continue experiments."
✅ **Good**: "Run Experiment 4 with 3 replicates by Friday."

---

## Differences from Paper Writing

| Aspect | Paper | Group Report |
|--------|-------|--------------|
| **Tone** | Formal | Conversational |
| **Structure** | IMRaD | Progress-focused |
| **Figures** | Publication-ready | Quick plots OK |
| **Failures** | Omitted | Included |
| **Next steps** | Future work (vague) | Action items (specific) |
| **Length** | 8-12 pages | 2-4 pages |

---

## Quality Checklist

Before finalizing report:

- [ ] **Background is clear** (PI can understand context)
- [ ] **Key findings highlighted** (not buried in details)
- [ ] **Figures are included** (every finding has visual evidence)
- [ ] **Blockers are explicit** (help needed is clear)
- [ ] **Next steps are concrete** (actionable, with deadlines)
- [ ] **Questions for discussion** (what needs group input)
- [ ] **Length is appropriate** (2-4 pages, not too long)

---

## Example: Complete Group Report

> # Research Progress Report
> **Date**: May 11, 2026
> **Project**: AdaptiveHarmony - Batch Correction for Rare Cell Types
> **Researcher**: Alice Chen
>
> ---
>
> ## Background
>
> We're developing AdaptiveHarmony, a batch correction method that preserves rare cell types. The goal is to integrate single-cell datasets while maintaining rare populations that are often lost with existing methods.
>
> ---
>
> ## This Week's Work
>
> ### Experiments Completed
> - Benchmark evaluation on 10 datasets (completed)
> - Ablation study on correction strength parameter (completed)
> - Case study on Human Cell Atlas bone marrow (in progress)
>
> ### Analysis Performed
> - Marker gene preservation analysis
> - Computational efficiency profiling
>
> ---
>
> ## Key Findings
>
> ### Finding 1: AdaptiveHarmony Preserves Rare Cell Types
>
> AdaptiveHarmony preserved 95% of rare cell type markers across 10 benchmark datasets, compared to 78% for Harmony and 81% for Seurat CCA (Figure 1).
>
> ![Marker preservation](figures/marker_preservation.png)
> **Figure 1**: Marker gene preservation for rare cell types.
>
> **Interpretation**: Cell-type-specific correction is critical for rare cell preservation.
>
> ### Finding 2: Method is Computationally Efficient
>
> AdaptiveHarmony processes 100,000 cells in 8 minutes, comparable to Harmony (7 min) and faster than scVI (25 min).
>
> ---
>
> ## Challenges & Blockers
>
> ### Blocker 1: Case Study Dataset Has Strong Batch Effects
> - **Issue**: HCA bone marrow dataset has very strong batch effects that obscure cell type structure
> - **Impact**: High - blocks case study completion
> - **Tried**: Adjusted theta parameter, tried different k values
> - **Help needed**: Should we use a different dataset or try more aggressive preprocessing?
>
> ---
>
> ## Next Steps
>
> ### This Week
> - [ ] Consult with PI about case study dataset choice
> - [ ] Complete ablation study analysis
> - [ ] Draft Results section
>
> ### Next Week
> - [ ] Finalize case study (with chosen dataset)
> - [ ] Generate all figures for manuscript
> - [ ] Draft Discussion section
>
> ---
>
> ## Questions for Discussion
>
> 1. Should we use a different dataset for the case study, or persist with HCA bone marrow?
> 2. Is 95% marker preservation sufficient, or should we aim higher?
> 3. When should we target for preprint submission?

---

## Success Metrics

A successful group report:
- Clearly communicates progress in 2-4 pages
- Highlights key findings with visual evidence
- Explicitly identifies blockers and help needed
- Provides concrete next steps with deadlines
- Facilitates productive discussion in lab meeting

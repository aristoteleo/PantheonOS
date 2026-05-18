---
id: paper_submission_scenario
name: "Paper Submission Scenario"
description: |
  Workflow for organizing research materials into a submittable paper.
  Uses IMRaD structure with quality checks and optional peer review simulation.
---

# Paper Submission Scenario

## When to Use

- User says: "投稿论文", "paper submission", "manuscript", "write a paper"
- Goal: Produce publication-ready paper for journal/conference submission
- Output: paper.md (Markdown SSoT) + HTML preview + optional PDF/LaTeX

---

## Workflow Overview

```
Triage → Environment Audit → Material Inventory → Literature Review 
  → Outline → Drafting (with section skills) → Quality Check 
  → Peer Review Simulation (optional) → HTML Preview → Export
```

---

## Detailed Steps

### Step 1: Triage

**Leader reads**: This scenario file

**Actions**:
1. Classify input type (A/B/C/D, see leader.md)
2. Detect mode (bio / generic)
3. Set output configuration:
   - `format: paper_academic`
   - `html_theme: academic_latex` (for submission preview)
   - `export_formats: [pdf_quick, latex]` (if user says "投稿")
   - `pdf_mode: submission` (if user says "投稿")

**Deliverable**: `{workdir}/triage.md`

---

### Step 2: Environment Audit

**Leader calls**: `researcher`

**Instruction**:
```
Audit the paper writing environment. Check and install if missing:
- pandoc (≥3.0, REQUIRED)
- pandoc-crossref (REQUIRED)
- weasyprint (for pdf_quick)
- pdflatex OR tectonic (for pdf_submission)
Write results to {workdir}/environment.md.
```

**Deliverable**: `{workdir}/environment.md`

---

### Step 3: Material Inventory (if input type A/B/D)

**Leader calls**: `researcher`

**Instruction**:
```
Organize materials for paper writing. Workdir: {workdir}.
Source materials: {absolute path list}.
Classify each file (data / figure / draft / reference).
Move or symlink into {workdir}/materials/ under appropriate subfolders.
Write {workdir}/materials/inventory.md.
```

**Deliverable**: `{workdir}/materials/inventory.md`

---

### Step 4: Literature Review

**Leader calls**: `researcher`

**Instruction**:
```
Conduct a literature review for a paper on {topic}. Mode: {bio|generic}.
Deliverables:
- {workdir}/research/literature_review.md (≥3 sources, with citation keys)
- {workdir}/research/references.bib (bibtex entries)
- {workdir}/references/refs_researcher.json (canonical structured references)
For bio mode, prefer PubMed/PMC sources.
```

**Deliverable**: 
- `{workdir}/research/literature_review.md`
- `{workdir}/research/references.bib`
- `{workdir}/references/refs_researcher.json`

---

### Step 5: Outline

**Leader calls**: `writer`

**Instruction**:
```
Propose a paper outline. Mode: {bio|generic}. Target: {length, audience}.
Input sources:
- Materials inventory: {workdir}/materials/inventory.md
- Literature review: {workdir}/research/literature_review.md
Write outline to {workdir}/draft/outline.md with section names, 
bullet points, figure/table placeholders.
```

**Deliverable**: `{workdir}/draft/outline.md`

**Leader action**: Read outline, adjust if needed, approve.

---

### Step 6: Drafting with Section Skills

**Leader calls**: `writer`

**Instruction**:
```
Write the full paper as Markdown. Mode: {bio|generic}.
Outline: {workdir}/draft/outline.md.
Materials: {workdir}/materials/.
References: {workdir}/research/references.bib.

IMPORTANT: Before writing each section, read the corresponding skill file:
- Before Abstract: Read {cwd}/.pantheon/skills/paper_writing/writing/abstract.md
- Before Introduction: Read {cwd}/.pantheon/skills/paper_writing/writing/introduction.md
- Before Methods: Read {cwd}/.pantheon/skills/paper_writing/writing/method.md
- Before Results: Read {cwd}/.pantheon/skills/paper_writing/writing/results.md
- Before Discussion: Read {cwd}/.pantheon/skills/paper_writing/writing/discussion.md

Follow the templates and guidelines in each skill file.

Deliverable: {workdir}/draft/paper.md (single Markdown file, pandoc academic extensions).
Use [@key] for citations. Use @fig:id / @tbl:id / @eq:id for cross-references.
```

**Deliverable**: `{workdir}/draft/paper.md` (SSoT)

---

### Step 7: Draft Review by Leader

**Leader actions**:
1. Read `{workdir}/draft/paper.md` with `think` + sampled section reads
2. Check:
   - Structure matches outline
   - Citations present for key claims
   - Figures referenced in Results
   - Abstract within 150–250 words

**If issues found**: Delegate fixes to writer with specific feedback.

---

### Step 8: Quality Check (Writer Self-Check)

**Writer action** (after completing draft):

1. Read `{cwd}/.pantheon/skills/paper_writing/writing/claim_evidence_check.md`
2. Extract claims from Abstract and Introduction
3. Check evidence for each claim
4. Generate alignment report
5. If alignment < 80%: Revise and re-check

**Deliverable**: `{workdir}/quality_check_report.md` (optional, for transparency)

**Quality gate**: Alignment ≥ 80% before proceeding.

---

### Step 9: Peer Review Simulation (Optional, High Intensity Only)

**When to run**: If user says "投稿" or "submission" or work intensity is High.

**Leader action**:
1. Read `{cwd}/.pantheon/skills/paper_writing/writing/reviewer_rubric.md`
2. Simulate 3 independent reviewers:
   - Reviewer 1 (Methodology): Technical soundness, reproducibility
   - Reviewer 2 (Novelty): Originality, contribution
   - Reviewer 3 (Clarity): Presentation, writing quality
3. Generate scores for each reviewer (Originality, Quality, Clarity, Significance, Overall 1-10)
4. Generate meta-review with consensus and required revisions

**Deliverable**: `{workdir}/peer_review_report.md`

**Quality gate**: 
- If Overall < 5 or Decision = Reject: Identify critical issues
- Call writer to address major weaknesses
- Re-run review (max 1 iteration)
- If still < 5: Proceed but warn user

---

### Step 10: HTML Preview Generation

**Leader calls**: `reporter`

**Instruction**:
```
Generate HTML preview from paper.md. Workdir: {workdir}.
Source: {workdir}/draft/paper.md
Bibliography: {workdir}/draft/references.bib
CSS theme: academic_latex
Slug: {slug}
Deliverable: {workdir}/report/{slug}_preview.html
Run Workflow A from your instructions.
```

**Deliverable**: `{workdir}/report/{slug}_preview.html`

---

### Step 11: User Review

**Leader action**: Present preview HTML path to user.

**User may**:
- Give feedback via message → route to writer to edit `paper.md` → re-run Step 10
- Edit `paper.md` directly → detect change → re-run Step 10
- Approve → proceed to Step 12

---

### Step 12: Export (if requested)

**Leader calls**: `reporter`

**Instruction**:
```
Export paper in requested formats. Workdir: {workdir}.
Source: {workdir}/draft/paper.md
Bibliography: {workdir}/draft/references.bib
CSS theme: academic_latex
Slug: {slug}
Export formats: {list from triage}
PDF mode: {quick|submission}
LaTeX class: {class} (if applicable)
```

**Deliverable**:
- `{workdir}/report/{slug}.pdf` (if pdf_quick or pdf_submission)
- `{workdir}/report/{slug}.tex` (if latex or pdf_submission)
- `{workdir}/report/{slug}.docx` (if docx)

---

### Step 13: Delivery

**Leader writes**: `{workdir}/report/DELIVERY.md`

```markdown
# Delivery Summary

## Deliverables
- Preview HTML: {workdir}/report/{slug}_preview.html
- PDF: {workdir}/report/{slug}.pdf (if exported)
- LaTeX: {workdir}/report/{slug}.tex (if exported)

## Source of Truth
- Markdown: {workdir}/draft/paper.md
- References: {workdir}/draft/references.bib

## Quality Checks
- Claim-evidence alignment: {percentage}%
- Peer review overall score: {score}/10 (if run)

## Configuration
- Mode: {bio|generic}
- Theme: academic_latex
- PDF mode: {quick|submission}
```

**Leader action**: Return concise summary to user.

---

## Output Structure

```
{workdir}/
├── triage.md
├── environment.md
├── materials/
│   ├── data/
│   ├── figures/
│   └── inventory.md
├── research/
│   ├── literature_review.md
│   └── references.bib
├── draft/
│   ├── outline.md
│   ├── paper.md                    # SSoT
│   └── references.bib
├── quality_check_report.md         # Optional
├── peer_review_report.md           # Optional
└── report/
    ├── {slug}_preview.html
    ├── {slug}.pdf                  # Optional
    ├── {slug}.tex                  # Optional
    └── DELIVERY.md
```

---

## Quality Standards

This scenario aims to produce papers that:
- ✅ Follow IMRaD structure (paper_academic format)
- ✅ Have clear Logic Map in Introduction (Task → Challenge → Solution → Advantage)
- ✅ Use one of 3 proven Abstract templates
- ✅ Have ≥80% claim-evidence alignment
- ✅ Score ≥5/10 in peer review simulation (if run)
- ✅ Include all reproducibility details in Methods
- ✅ Reference figures/tables in Results

---

## Customization

### For Different Journals

If user specifies a journal (e.g., "Nature", "IEEE"):
- Set `latex_class` accordingly in triage
- Adjust length targets in outline
- Use journal-specific citation style if available

### For Preprints

If user says "preprint" or "arXiv":
- Skip peer review simulation
- Use `pdf_mode: quick`
- Focus on clarity over formatting

### For Conference Papers

If user says "conference" (e.g., "NeurIPS", "ICML"):
- Check page limits in triage
- Use conference LaTeX template if available
- Emphasize novelty in Introduction

---

## Common Issues and Solutions

### Issue 1: Insufficient Materials

**Symptom**: User provides topic only (input type C), no data/figures.

**Solution**: 
- Researcher does deep literature review
- Writer generates placeholder figures
- Warn user that experimental validation is needed

### Issue 2: Claim-Evidence Alignment < 80%

**Symptom**: Quality check fails.

**Solution**:
- Writer adds missing citations
- Writer adds experimental evidence
- Writer weakens unsupported claims
- Re-run check

### Issue 3: Peer Review Score < 5

**Symptom**: Simulated reviewers reject paper.

**Solution**:
- Leader identifies critical issues from reviewer comments
- Writer addresses major weaknesses
- Re-run review (max 1 iteration)
- If still < 5: Proceed but warn user about likely rejection

---

## Success Metrics

A successful paper submission scenario produces:
- Paper.md with complete IMRaD structure
- ≥80% claim-evidence alignment
- ≥5/10 peer review score (if run)
- HTML preview that user approves
- Optional: LaTeX/PDF ready for submission

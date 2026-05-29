---
id: paper_writing_workflow
name: Paper Writing Workflow
description: |
  Workflow phases for paper-writing tasks: triage, material inventory,
  research question, literature review, paper outline, data analysis summary,
  figure storyline, reader testing, and finalize packet. Each phase is a
  short contract — read the relevant rows for the current task only.
tags: [paper_writing, workflow, triage, outline]
---

# Paper Writing Workflow

Workflow phases decide the order of work. They do not provide final prose
style or visual themes. Use only the phases relevant to the current
scenario; not every task needs every phase.

| Phase | When to run | Output file |
|---|---|---|
| 1. Triage | every task; whenever user intent changes | `{workdir}/triage.md` |
| 2. Material inventory | user supplied files, notes, data, figures, comments | `{workdir}/materials/inventory.md` |
| 3. Research question | early papers, grants, vague topics; before outline | `{workdir}/research_question.md` |
| 4. Literature review | claims need citation grounding or novelty positioning | `{workdir}/literature_matrix.md` |
| 5. Paper outline | before drafting any full manuscript, grant, or report | `{workdir}/outline.md` |
| 6. Data analysis summary | user provides result tables, plots, statistics, experiment notes | `{workdir}/results_table.md` |
| 7. Figure storyline | figures/tables carry the core story | `{workdir}/figures_plan.md` |
| 8. Reader testing | before finalizing high-stakes documents or after major rewrites | `{workdir}/reader_test.md` |
| 9. Finalize packet | before ending a full task | `{workdir}/final_state.md` |

Phases tied to a single scenario live in that scenario file:

- Revision / rebuttal loop → [../scenarios/revision_response.md](../scenarios/revision_response.md)
- Knowledge lineage / novelty audit → [../scenarios/grant_proposal.md](../scenarios/grant_proposal.md)

---

## 1. Triage

Use at the start of every paper-writing task and whenever user intent changes.

### Output

Create or update `triage.md`:

```yaml
scenario_id: paper_submission
format_id: journal_article
theme_id: editable_article
mode: report              # report (default) | submission — see Mode section in SKILL.md
language: zh
audience: reviewers
source_materials:
  - materials/inventory.md
output:
  markdown_source: draft/paper.md
  html_path: report/paper_preview.html
  editable_html: true
  pdf: true
  docx: false
  latex: false
constraints:
  venue_profile: null
  page_size: A4
  word_limits: null
quality_gates:
  - manuscript_coverage
  - format_lint
  - reproducibility
  - html_editability_check
  # submission mode only:
  # - claim_evidence_check
  # - reviewer_rubric
  # - reporting_guideline_check  (clinical only)
```

### Checks

- If a UI label and user text conflict, prefer the user's latest text and note the conflict in `constraints`.
- If target venue is unknown, keep `format_id` generic but do not ignore any provided page/word/style limits.
- If the user only asks for a small section edit, still record scenario and gates, but output only the requested section plus relevant quality notes.

---

## 2. Material Inventory

Use after triage when the user has supplied files, notes, datasets, figures, or reviewer comments.

### Output

Write `materials/inventory.md` with this table:

| Material ID | Path/source | Type | Relevant sections | Claims supported | Gaps/risks |
|---|---|---|---|---|---|
| M001 | user file or note | draft/data/figure/PDF/comment | Methods, Results | C1, C4 | missing sample size |

### Rules

- Do not leave attachments only in chat context. Give each useful item an ID.
- Mark unreadable or missing files explicitly.
- Separate user-provided facts from model inference.
- Map reviewer comments to `revision_response` IDs, not generic notes.

---

## 3. Research Question

Use before outline writing for early papers, grants, and vague topics.

### Output

| Item | Requirement |
|---|---|
| Field context | What is already known |
| Gap | What remains unknown or insufficient |
| Question | Specific, testable question |
| Hypothesis/aim | What the work claims or tests |
| Boundary | What the work will not claim |
| Evidence needed | Data, figure, citation, or user material needed |

### Rules

- Do not treat a broad topic as a research question.
- For grants, produce aim-level statements and feasibility notes.
- For papers, map each contribution to at least one evidence need.

---

## 4. Literature Review

Use when claims need citation grounding, novelty needs positioning, or the user asks for related work.

### Output — literature matrix

| Paper ID | Citation/URL | Theme | What it supports | Limitation/gap | Candidate claim IDs |
|---|---|---|---|---|---|

### Rules

- Group by theme, method, finding, or debate; do not merely list papers by year.
- Classify support as `strong`, `partial`, `background`, or `conflicting`.
- If a paper only matches the topic but not the exact claim, label it `background`, not `strong`.
- For systematic reviews, trigger the reporting-guideline check (PRISMA) in [../SKILL.md](../SKILL.md).

---

## 5. Paper Outline

Use before drafting any full manuscript, grant, or report.

### Output — two synchronized views

```text
paper_view:
  section -> purpose -> paragraph roles -> expected figures/tables
evidence_view:
  section -> claim IDs -> evidence IDs -> missing evidence -> risk
```

### Exit criteria

- Each section has a purpose.
- Core claims are scoped and numbered.
- Evidence gaps are visible before writing.
- The outline does not include raw logs, implementation chatter, or unfiltered analysis notes.

---

## 6. Data Analysis Summary

Use when the user provides result tables, plots, statistics, or experiment notes.

### Output

| Result ID | Question | Data/readout | Observation | Statistical result | Interpretation | Figure/table |
|---|---|---|---|---|---|---|

### Rules

- Keep observation separate from interpretation.
- Include sample size, grouping, statistical method, and uncertainty when known.
- If a statistic is missing, mark it; do not invent p-values or effect sizes.

---

## 7. Figure Storyline

Use when figures/tables carry the core story. Do not force AI images or a fixed number of figures; create figure plans only when evidence requires them.

### Output

| Figure | Question | Main claim | Panels/metrics | Evidence source | Caption message | Review risk |
|---|---|---|---|---|---|---|

### Rules

- One figure answers one scientific question.
- One panel supports one sub-claim or visual comparison.
- Captions state conclusion, evidence, and key condition, not only plot type.
- Missing raw data or unclear statistics must be flagged before drafting.

---

## 8. Reader Testing

Use before finalizing high-stakes documents or after major rewrites.

### Test questions (target reader's perspective)

1. What problem is this solving?
2. What is the main contribution or ask?
3. What evidence supports it?
4. What remains uncertain?
5. What should the reader do next?

### Output

| Reader test item | Pass/fail | Evidence in draft | Fix |
|---|---|---|---|

### Rules

- Do not use author intention as proof of clarity. The draft must contain the answer.
- For talks, test spoken takeaway and figure sequence.
- For grants, test whether the aim and feasibility are recoverable in one pass.

---

## 9. Finalize Packet

Use before ending a full paper-writing task. Create a concise final state block:

| Item | Value |
|---|---|
| Main draft | `{workdir}/draft/paper.md` |
| Editable HTML | `{workdir}/report/<slug>_preview.html` |
| Quality reports | list |
| Open evidence gaps | list or none |
| Unsupported claims removed/downgraded | list |
| User decisions still needed | list |
| Suggested next action | concrete next step |

### Rules

- Do not hide unresolved risks.
- Preserve enough state that another agent can resume without rereading the full conversation.

---

Sources: Anthropic doc-coauthoring (context gathering, reader testing),
DeepScientist paper-outline / review, nature-figure, nature-citation,
mattpocock writing-shape, K-Dense scientific-writing / literature-review /
research-grants, lishix520 strategist, academic-pipeline, PaperQA,
research-paper-writing.

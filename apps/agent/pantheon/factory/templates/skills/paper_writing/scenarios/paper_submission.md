---
id: paper_submission_scenario
name: Paper Submission Scenario
description: |
  General manuscript submission route for paper-writing tasks: organize
  materials into an IMRaD draft, run quality gates, optionally simulate peer
  review, and package an editable HTML preview plus optional LaTeX/PDF.
tags: [paper_writing, manuscript, submission]
---

# Paper Submission Scenario

Use when the user asks to write, revise, or package a paper but has not locked
a journal- or conference-specific structure. For locked targets see
[journal_article.md](./journal_article.md) or [conference_paper.md](./conference_paper.md).

## Contract

| Field | Value |
|---|---|
| Trigger | "paper", "manuscript", "投稿", "论文写作", "write a paper", "submission" |
| Inputs | research materials, partial draft, figures/tables, references, target venue if known |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Material Inventory + Literature Review + Paper Outline sections), [../writing/SKILL.md](../writing/SKILL.md) |
| Outputs | `{workdir}/triage.md`, `{workdir}/draft/paper.md`, `{workdir}/report/<slug>_preview.html`, quality reports under `{workdir}/quality/` |
| Format | `journal_article` or `conference_paper` (see the Scenario Format Index in the family root); HTML output must follow [../SKILL.md](../SKILL.md) |
| Theme | `kami_academic` by default ([../themes/kami_academic.md](../themes/kami_academic.md)) |
| Gates | `claim_evidence_check`, `reviewer_rubric`, `format_lint`, `manuscript_coverage_check`, `html_editability_check` |
| Forbidden | choosing a venue-agnostic structure when venue constraints are provided; venue-specific overrides without recording them in `triage.md` |

## Section Structure (IMRaD)

| Section | Required | Notes |
|---|---|---|
| Title | yes | searchable, precise, no inflation |
| Abstract | yes | 150–250 words, no citations, choose template from [../writing/abstract.md](../writing/abstract.md) |
| Keywords | if required | 3–6 keywords |
| Introduction | yes | Logic Map: Task → Challenge → Solution → Advantage ([../writing/introduction.md](../writing/introduction.md)) |
| Related Work | optional | themed positioning, not chronological dump |
| Methods | yes | reproducibility checklist ([../writing/method.md](../writing/method.md)) |
| Results | yes | each subsection ≥ 1 figure/table reference ([../writing/results.md](../writing/results.md)) |
| Discussion | yes | Interpretation → Comparison → Limitations → Future ([../writing/discussion.md](../writing/discussion.md)) |
| Conclusion | optional | not abstract repetition |
| Data / Code Availability | yes for empirical work | see [./journal_article.md](./journal_article.md) (Data and Code Availability section) |
| References | yes | grounded via [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) (Citation Grounding section) |
| Acknowledgements | optional | — |

## Default Path

```text
triage → material_inventory → literature_review → evidence_registry
  → figure_storyline (if figures are central) → paper_outline → draft
  → claim_evidence_check → manuscript_coverage_check → format_lint
  → reviewer_rubric (optional, high-stakes) → editable HTML preview
  → finalize_packet
```

## Scenario-Specific Rules

- **Claim-evidence alignment ≥ 80%** before declaring the draft complete.
- **Peer review simulation** is optional but strongly recommended when the user
  signals "投稿" / "submission" / high stakes; if Overall < 5, identify critical
  issues and revise once before proceeding.
- **Logic Map** must align Introduction's gap statement with the contribution
  claim made in the Abstract.
- **Reproducibility** information lives in Methods, not Results; move
  parameter tables to supplementary if Methods grows beyond the venue's limits.

## Customization

- **Preprint / arXiv**: skip peer review simulation; use `pdf_mode: quick`;
  prioritize clarity over format polish.
- **Specific journal**: set `format_id: journal_article` and lock LaTeX class
  in `triage.md`; adjust length targets in the outline phase.
- **Specific conference**: set `format_id: conference_paper`; record page limit
  in `triage.md` constraints; emphasize novelty in Introduction.

## Success Metrics

- IMRaD structure complete; all required sections present.
- ≥ 80% claim-evidence alignment.
- ≥ 5/10 peer review score (if simulated).
- HTML preview meets the editable-block contract and renders standalone.
- Optional LaTeX / PDF compiles cleanly with the chosen template.

Sources: PR 104 paper_submission scenario distillation, academic-paper/SKILL.md,
research-paper-writing/SKILL.md, paper-outline/SKILL.md, paper-review.md.

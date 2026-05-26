---
id: group_report_scenario
name: Group Report Scenario
description: |
  Workflow for turning scattered research progress into a clear, structured
  narrative for lab meetings, weekly reports, or rotating progress updates.
tags: [paper_writing, group_report, lab_meeting, progress]
---

# Group Report Scenario

Use when the user needs to compile recent research activity into a scan-friendly
report aimed at lab members, advisors, or collaborators rather than reviewers.

## Contract

| Field | Value |
|---|---|
| Trigger | "组会", "组会汇报", "lab meeting", "progress report", "weekly report", "monthly update" |
| Inputs | recent experimental results, figures, meeting notes, blockers, planned next steps |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Material Inventory + Data Analysis Summary + Figure Storyline sections), [../writing/SKILL.md](../writing/SKILL.md) |
| Outputs | `{workdir}/materials/inventory.md`, `{workdir}/draft/paper.md`, `{workdir}/report/<slug>_preview.html` |
| Format | `group_report` |
| Theme | `kami_academic` |
| Gates | `evidence_summary` review, `format_lint`, `html_editability_check` |
| Forbidden | overclaiming preliminary findings; padding to fake progress; dumping raw data without a question/conclusion frame |

## Section Structure

The default `group_report` format keeps sections short and scan-friendly:

| Section | Required | Notes |
|---|---|---|
| Progress | yes | what was done since the last report |
| Core Question | yes | the research question driving this period |
| Evidence | yes | results, figures, tables; one figure → one takeaway |
| Current Conclusion | yes | what we now believe, with confidence level |
| Blockers | yes | things that are stuck and what's needed to unblock |
| Discussion Questions | yes | 2–5 questions to put to the group |
| Next Plan | yes | concrete next steps with rough timeline |

Short paragraphs and bullet lists; reading time target 5–10 minutes.

## Three Common Shapes

Pick the one that matches the period being reported:

- **Experiment-focused**: Background → This Period's Work → Key Findings → Blockers → Next Steps
- **Milestone-focused**: Project Goal → Progress vs Plan → Key Results → Challenges → Timeline Update
- **Multi-project**: Project A → Project B → Cross-project Insights → Resource Needs → Priorities

## Default Path

```text
material_inventory → data_analysis_summary → figure_storyline
  → paper_outline (group_report shape) → draft → format_lint
  → editable HTML → finalize_packet
```

## Scenario-Specific Rules

- **Confidence labels**. Every conclusion gets a label: "established",
  "supported", "preliminary", "speculative". Do not present preliminary
  findings as established.
- **Each figure has a takeaway**. A figure without a one-sentence takeaway
  belongs in supplementary, not in the report body.
- **Blockers are not optional**. If there are no blockers, say so explicitly —
  reviewers and advisors interpret a missing blockers section as hidden
  problems.
- **Discussion questions, not statements**. The "Discussion Questions" section
  must contain real open questions, not rhetorical hooks.

## Customization

- **First report**: lead with one paragraph of project-wide context before
  Progress.
- **Crisis report**: collapse into Blockers + Decisions Needed only; defer the
  full structure to the next normal cycle.
- **Cross-team / collaboration update**: add a "Asks" section listing what each
  collaborator needs to do before the next report.

## Success Metrics

- Reading time 5–10 minutes for the body.
- Every figure has a takeaway sentence.
- Blockers section is present, even if empty.
- Discussion questions section contains 2–5 real questions.
- HTML output renders standalone and meets the editable-block contract.

Sources: PR 104 group_report distillation, academic-research-skills (Apache 2.0).

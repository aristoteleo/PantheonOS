---
id: paper_writing_workflow
name: Paper Writing Workflow
description: Workflow index for research writing phases, triage, outlining, evidence boundaries, and finalize packets.
tags: [paper_writing, workflow, triage, outline]
---

# Workflow Skills

Workflow skills decide the order of work. They do not provide final prose style
or visual themes.

| Need | File |
|---|---|
| Start or reroute any task | [triage.md](./triage.md) |
| Inventory user materials | [material_inventory.md](./material_inventory.md) |
| Position literature and claims | [literature_review.md](./literature_review.md) |
| Define question/aims | [research_question.md](./research_question.md) |
| Split manuscript view from evidence view | [paper_outline.md](./paper_outline.md) |
| Summarize experiments/data | [data_analysis_summary.md](./data_analysis_summary.md) |
| Build figure logic | [figure_storyline.md](./figure_storyline.md) |
| Test reader understanding | [reader_testing.md](./reader_testing.md) |

Phases tied to a single scenario live in that scenario file:

- Revision / rebuttal loop → [../scenarios/revision_response.md](../scenarios/revision_response.md)
- Knowledge lineage / novelty audit → [../scenarios/grant_proposal.md](../scenarios/grant_proposal.md)

## Finalize Packet

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

Rules:

- Do not hide unresolved risks.
- Preserve enough state that another agent can resume without rereading the
  full conversation.

Sources for inlined section: academic-pipeline/SKILL.md, DeepScientist
review/SKILL.md.

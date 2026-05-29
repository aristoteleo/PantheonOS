---
id: paper_writing_scenarios
name: Paper Writing Scenario Router
description: |
  Scenario router for paper-writing tasks. Use after root triage to select
  paper submission, journal article, conference paper, grant proposal, lab
  report, group report, talk/workshop, or revision-response behavior.
tags: [paper_writing, scenarios, routing]
---

# Scenario Router

Scenario files decide the task shape. They do not write prose directly; they
select workflow, format, theme, and quality gates.

| Scenario | File | Use when |
|---|---|---|
| `paper_submission` | [paper_submission.md](./paper_submission.md) | User asks for a manuscript or general paper submission |
| `journal_article` | [journal_article.md](./journal_article.md) | User targets a journal or article type |
| `conference_paper` | [conference_paper.md](./conference_paper.md) | User targets a conference/workshop paper |
| `grant_proposal` | [grant_proposal.md](./grant_proposal.md) | User asks for grant/proposal/funding application |
| `lab_report` | [lab_report.md](./lab_report.md) | User asks for experiment/lab report |
| `group_report` | [group_report.md](./group_report.md) | User asks for group meeting, weekly report, progress report |
| `conference_talk` | [conference_talk.md](./conference_talk.md) | User asks for talk, speech, slides narrative, speaker notes |
| `workshop_share` | [workshop_share.md](./workshop_share.md) | User asks for workshop sharing, tutorial, reproducible notes |
| `revision_response` | [revision_response.md](./revision_response.md) | User provides reviewer/editor comments or asks for rebuttal |

For every scenario, write the chosen IDs into `triage.md` before drafting.

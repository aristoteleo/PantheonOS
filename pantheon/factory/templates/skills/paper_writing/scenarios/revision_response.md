---
id: revision_response_scenario
name: Revision Response Scenario
description: |
  Workflow for converting reviewer comments into a point-by-point response
  letter plus a revised manuscript with reviewer-driven changes traceable
  back to specific comments.
tags: [paper_writing, rebuttal, revision, reviewer]
---

# Revision Response Scenario

Use when the user has received reviewer comments on a previously submitted
manuscript and needs to produce both a revised draft and a response letter.

## Contract

| Field | Value |
|---|---|
| Trigger | "审稿返修", "reviewer comments", "revision", "rebuttal", "response letter" |
| Inputs | reviewer comments (txt / PDF / JSON), the original `paper.md`, optional editor letter |
| Read next | [../writing/response_letter.md](../writing/response_letter.md), [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md), [../SKILL.md](../SKILL.md) (Format Lint section) |
| Outputs | `{workdir}/parsed_comments.json`, `{workdir}/revision_roadmap.md`, `{workdir}/draft/paper.md` (revised), `{workdir}/draft/response_letter.md`, `{workdir}/report/<slug>_response.html` |
| Format | `revision_response` |
| Theme | `kami_academic` |
| Gates | `response_consistency_check` (below), `claim_evidence_check`, `format_lint`, `html_editability_check` |
| Forbidden | dropping or merging reviewer comments; promising changes that are not reflected in the revised draft; fabricating new experiments to satisfy reviewers |

## Comment Parsing Schema

```json
{
  "reviewer_1": [
    {"id": "R1-C1", "text": "...", "section": "Methods",
     "severity": "critical|important|nice-to-have",
     "type": "methodology|novelty|clarity|ethical|editorial",
     "actionable": true}
  ]
}
```

Every comment must end up in the response letter. Use the response unit format
defined in [../writing/response_letter.md](../writing/response_letter.md):

```text
**Reviewer X, Comment Y**
Comment: <verbatim>
Response: <thank, acknowledge, address>
Changes Made: <pointer into revised paper.md, e.g. "§Methods 2.3, lines 145–158">
Status: addressed | partially addressed | declined-with-rationale
```

## Revision Loop

The full loop, after triage:

1. **Preserve every comment verbatim** and assign IDs (R1-C1 form).
2. **Classify route per comment**: `text`, `evidence`, `experiment`, `analysis`,
   `format`, `claim_downgrade`, `decline_with_reason`, `needs_user_input`.
3. **Build revision roadmap**: which comments to address in which order,
   estimated effort, dependencies between comments.
4. **Revise the draft** with trackable text deltas (commit-style diffs or
   marked-up Markdown).
5. **Write the response letter** using the unit format above.
6. **Run the response consistency check** (below).

Roadmap output table:

| Comment ID | Reviewer text | Class | Required action | Manuscript change | Response status |
|---|---|---|---|---|---|

## Default Path

```text
parse_comments → classify_and_prioritize → revision_roadmap
  → revise paper.md (writing/) → response_letter (writing/response_letter.md)
  → response_consistency_check (below) → claim_evidence_check → format_lint
  → editable HTML → finalize_packet
```

## Scenario-Specific Rules

- **Every comment preserved**. The response letter must contain a unit for each
  numbered comment, including ones that are declined — declines must include a
  rationale and a citation or evidence reason.
- **Changes Made must be locator-precise**. "Updated Methods" is not enough;
  point to a section, paragraph, line range, or commit-style diff anchor.
- **Manuscript / response consistency**. Run the Response Consistency Check
  section below: every "Changes Made" claim must correspond to a real edit in
  `paper.md`, and
  every substantive edit in `paper.md` should map back to a comment or be
  noted as an editor-driven improvement.
- **No silent claim weakening**. If reviewer pressure forces a claim to be
  downgraded, surface that in both the response letter and the revised
  Discussion / Limitations.
- **No fabricated experiments**. If a reviewer asks for an experiment that
  cannot be run, decline with a clear rationale; do not invent results.

## Customization

- **Major revision**: re-run the full quality gate stack on the revised draft;
  treat it like a new submission.
- **Minor revision / camera-ready**: skip peer review simulation; focus on
  response letter precision and format lint.
- **Editor-only letter**: if there is an editor letter without per-reviewer
  comments, structure responses by editor-issue id instead.

## Response Consistency Check

Run before delivering the rebuttal package. Output table:

| Comment ID | Has response | Change claimed | Draft location | Status | Issue |
|---|---|---|---|---|---|

Rules:

- Every reviewer or editor comment must appear.
- Every response must state whether text changed.
- Claimed changes must be present in the revised draft (verify the locator).
- Declined changes must include a defensible reason — scientific, scope, data
  availability, or out-of-scope.
- Orphan comments (no response) and orphan edits (no comment justifying them)
  are both flagged.

## Success Metrics

- Every reviewer comment appears in the response letter with a status.
- Every "Changes Made" pointer resolves to a real location in the revised
  `paper.md`.
- Response consistency check shows no orphan comments and no orphan edits.
- HTML response output meets the editable-block contract.

Sources: PR 104 revision_response distillation, academic-research-skills
(Apache 2.0), DeepScientist rebuttal/SKILL.md.

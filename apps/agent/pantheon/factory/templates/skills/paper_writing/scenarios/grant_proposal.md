---
id: grant_proposal_scenario
name: Grant Proposal Scenario
description: |
  Workflow for packaging a research idea into a grant or funding proposal:
  define the central question and gap, structure 2–3 specific aims with
  approach and expected outcomes, and connect significance, innovation,
  feasibility, timeline, and budget into a coherent narrative.
tags: [paper_writing, grant, proposal, funding]
---

# Grant Proposal Scenario

Use when the user is preparing a funding application (NIH, NSF, ERC, NSFC,
foundation grants, internal seed funding). The goal is convincing reviewers
that the question is important, the approach is feasible, and the team can
deliver — not reporting completed work.

## Contract

| Field | Value |
|---|---|
| Trigger | "基金申请", "grant proposal", "funding application", "NIH", "NSF", "ERC", "NSFC", "项目申请书" |
| Inputs | research idea, preliminary data, prior publications, target program/RFA, deadline, budget cap, team CVs |
| Read next | [../workflow/SKILL.md](../workflow/SKILL.md) (Research Question + Literature Review sections), [../writing/SKILL.md](../writing/SKILL.md) (see Knowledge Lineage Audit below for novelty claims) |
| Outputs | `{workdir}/research_question.md`, `{workdir}/specific_aims.md`, `{workdir}/draft/proposal.md`, `{workdir}/draft/budget_justification.md`, `{workdir}/report/<slug>_proposal.html` |
| Format | `grant_application` |
| Theme | `kami_academic` |
| Gates | gap-aim-route consistency, word/page limit lint, `claim_evidence_check` (preliminary data), `reviewer_rubric`, `format_lint` |
| Forbidden | aims that don't address the central question; promising work outside the team's competence; ignoring program-specific constraints (page/word limits, font, margin) |

## Section Structure (program-agnostic)

| Section | Required | Notes |
|---|---|---|
| Title | yes | concrete, technical, no buzzword stacking |
| Abstract / Project Summary | yes | layperson + technical summary, often two short blocks |
| Background | yes | what is known, who cares |
| Gap | yes | what is missing, why it matters now |
| Specific Aims | yes | 2–3 aims, each with objective, rationale, approach, expected outcome |
| Significance | yes | why solving this matters to the field and beyond |
| Innovation | yes | conceptual / methodological / paradigm advances |
| Approach / Research Plan | yes | per-aim methods, milestones, alternative strategies |
| Feasibility | yes | preliminary data, team expertise, environment |
| Timeline | yes | per-aim months, dependencies between aims |
| Expected Outcomes | yes | scientific deliverables and broader impacts |
| Team | yes | PI, co-PIs, key personnel, roles |
| Budget | yes | cost categories with justification |
| References | yes | grounded via [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) (Citation Grounding section) |

Always overlay program-specific section names and limits (NIH "Specific Aims"
page, NSF "Project Description"/"Broader Impacts", NSFC innovation/feasibility).

## Aim Pattern

A common 3-aim shape:

- **Aim 1 — Foundational**: develop the method, system, or framework.
- **Aim 2 — Validation**: test against real data / benchmarks / cohorts.
- **Aim 3 — Application**: deliver biological, clinical, or societal impact.

Aim 3 should not depend on Aim 2's success in a way that makes Aim 2 a
single point of failure; reviewers look for risk-mitigation across aims.

## Default Path

```text
research_question → literature_review → gap analysis
  → specific_aims (2–3) → significance / innovation
  → research_plan (per-aim approach + alternatives)
  → feasibility (preliminary data, team, environment)
  → timeline + budget → claim_evidence_check (preliminary data)
  → reviewer_rubric (program-style mock review)
  → format_lint (page/word/font/margin) → editable HTML
  → finalize_packet
```

## Scenario-Specific Rules

- **Gap–Aim–Route consistency**. Every aim must close part of the stated gap;
  every approach step must serve its aim. Run a self-check: pull each Aim
  paragraph, list the verb-objects, and confirm they appear in Approach.
- **Risk + alternatives in every aim**. State the most likely failure mode
  and a fallback strategy. Reviewers reward this; absence reads as naive.
- **Preliminary data is evidence, not promotion**. Apply
  [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) — if
  preliminary results don't support feasibility, downgrade or move them to
  motivation.
- **Hard limits are hard**. Page count, word count, font, margin, line
  spacing, references-per-page caps — all program-specific. Lint before
  submission; over-limit grants are desk-rejected.
- **Layperson summary is mandatory** for most programs. Test it on a
  non-specialist before submission.
- **No fabricated commitment letters or collaborator quotes**. Real letters
  on file or omit the collaboration claim.

## Knowledge Lineage Audit

Run before any "first to do this" or "novel approach" claim in Innovation or
Specific Aims. Output table:

| Idea/claim | Prior lineage | Similar attempts | Failed attempts | Difference now | Novelty boundary |
|---|---|---|---|---|---|

Rules:

- Treat "new" as a claim requiring evidence.
- Search for older names, adjacent fields, negative results, and revived ideas.
- If an idea repeats a known route with a new dataset/tool, say so precisely.
- Use lineage results to narrow contributions, not to inflate them. A grant
  reviewer who can name two prior attempts you missed will reject for naivety.

## Customization

- **NIH-style**: pull Specific Aims onto its own one-page section; structure
  Approach by Aim with subsections; include Rigor + Reproducibility statement.
- **NSF-style**: emphasize Intellectual Merit + Broader Impacts as named
  sections; condense per-aim methods; include data management plan.
- **ERC-style**: lead with high-risk / high-gain framing; foreground PI
  trajectory; structure as B1 (extended synopsis) + B2 (full proposal).
- **NSFC-style**: surface 关键科学问题 (key scientific questions) and 技术
  路线 (technical route) as explicit sections; align innovation with
  feasibility.
- **Foundation / seed grant**: shrink to single aim; emphasize concrete
  6–18 month deliverables.

## Success Metrics

- 2–3 aims that collectively close the stated gap.
- Each aim has objective / rationale / approach / outcome / risk + fallback.
- Preliminary data passes claim-evidence check.
- Mock reviewer score ≥ 5/10 across novelty, feasibility, significance.
- Page / word / format constraints all within program limits.
- HTML output meets the editable-block contract.

Sources: PR 104 grant_proposal distillation, NIH/NSF program guidance,
DeepScientist proposal/SKILL.md.

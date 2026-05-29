---
id: paper_writing_section_drafting
name: Evidence-Bound Section Drafting
description: |
  Section-level writing skill for evidence-bound academic prose: IMRaD papers,
  grants, reports, talks, and response letters. Indexes section-specific
  templates (abstract, introduction, method, results, discussion) and
  pre-submission quality protocols (claim-evidence, reviewer rubric, response
  letter).
tags: [paper_writing, writing, imrad]
---

# Evidence-Bound Section Drafting

Use only after triage, outline, and evidence boundaries exist (see
[../workflow/SKILL.md](../workflow/SKILL.md) and
[../SKILL.md](../SKILL.md)).

## Drafting Rules

- Write `draft/paper.md` as the content source of truth.
- Keep each core claim within `claim_evidence_map.md`.
- Use `missing` evidence to ask for material, downgrade, or remove claims.
- Do not invent data, citations, mechanisms, reviewer changes, or availability
  statements.
- For major rewrites, shape from raw material to candidate openings to
  paragraph-by-paragraph structure before polishing.

## Section Roles (quick reference)

| Section | Role | Quality gate |
|---|---|---|
| Title | searchable, precise, not inflated | no vague clever title |
| Abstract | problem, gap, method, key result, meaning | no result-free impact claim |
| Introduction | known → gap → insufficiency → approach → contribution | gap and contribution align |
| Related work | themed positioning | not chronological paper dump |
| Methods | reproducible protocol | data/software/parameters/statistics clear |
| Results | question → method → observation → quantitative result → interpretation | each result points to figure/table/data |
| Discussion | finding, relation to literature, mechanism, limits, future | no new data |
| Limitations | honest boundary and risk | no hidden fatal flaw |
| Conclusion | contribution and boundary | not abstract repetition |

## Section-Specific Skills

Read the corresponding skill file before drafting each section.

| Section | File | Focus |
|---|---|---|
| Abstract | [abstract.md](./abstract.md) | Three proven templates (Challenge→Contribution, Challenge→Insight→Contribution, Multiple Contributions); 150–250 words; no citations |
| Introduction | [introduction.md](./introduction.md) | Logic Map: Task → Challenge → Solution → Advantage; backward writing |
| Methods | [method.md](./method.md) | Reproducibility checklist: software versions, parameters, hardware, data/code statements |
| Results | [results.md](./results.md) | Each subsection ≥1 figure/table reference; claim → evidence; present tense |
| Discussion | [discussion.md](./discussion.md) | Four-part structure: Interpretation → Comparison → Limitations → Future |
| Response Letter | [response_letter.md](./response_letter.md) | Reviewer-comment-to-response unit format |

## Quality Protocols

| Skill | File | Purpose |
|---|---|---|
| Claim-Evidence Check | [claim_evidence_check.md](./claim_evidence_check.md) | Verify every major claim has supporting evidence; target ≥80% |
| Reviewer Rubric (NeurIPS-style) | [reviewer_rubric.md](./reviewer_rubric.md) | 6-dimension peer-review simulation with worked example |

## Usage

1. Before writing a section, read the corresponding section skill file.
2. Apply the templates and structural guidelines.
3. After the full draft is complete, run [claim_evidence_check.md](./claim_evidence_check.md).
4. If alignment < 80%, revise claims (add evidence, downgrade, or remove) and
   re-run.
5. For submissions, grants, or revision responses, optionally simulate peer
   review using [reviewer_rubric.md](./reviewer_rubric.md).

## Quality Standards

- **Clarity**: clear, concise, unambiguous language
- **Evidence**: every claim backed by citation, data, or figure
- **Structure**: logical flow and organization
- **Reproducibility**: sufficient detail for replication
- **Impact**: clear contribution and boundary

Sources: Research-Paper-Writing-Skills (Master-cai, MIT), AI-Scientist
(SakanaAI, MIT), ResearAI writer.md, K-Dense scientific-writing/SKILL.md,
mattpocock writing-shape/SKILL.md.

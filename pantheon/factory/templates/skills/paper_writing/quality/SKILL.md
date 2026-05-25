---
id: paper_writing_quality
name: Paper Writing Quality Gates
description: |
  Quality gate index for claim/evidence audit, reviewer simulation, reporting
  guidelines, citation checks, editability, reproducibility, manuscript
  coverage, response consistency, format lint, and skill-structure audit.
tags: [paper_writing, quality, review]
---

# Quality Gates

Quality skills produce reports and risk flags. They are guardrails, not
gatekeepers — flag issues and suggest fixes; do not silently rewrite a draft
to hide problems.

## Philosophy

- Aim for **≥80% compliance**, not 100%.
- Prioritize critical issues (missing data, unsupported claims, fabricated
  citations) over cosmetic formatting.
- Each gate produces an inspectable report; downstream consumers decide whether
  to revise, downgrade, or accept the risk.

## Gates

| Gate | File | Default trigger | Source |
|---|---|---|---|
| Claim / evidence audit | [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) | all high-stakes writing | Research-Paper-Writing-Skills |
| Reviewer simulation (audit table) | [reviewer_rubric.md](./reviewer_rubric.md) | papers, grants, rebuttals | DeepScientist, K-Dense |
| Reviewer simulation (NeurIPS-style) | [../writing/reviewer_rubric.md](../writing/reviewer_rubric.md) | pre-submission scoring | AI-Scientist |
| Citation audit | [citation_check.md](./citation_check.md) | manuscripts with references | — |
| Reproducibility audit | [reproducibility_check.md](./reproducibility_check.md) | methods, lab reports, workshops | nature-polishing |
| Reporting guideline check | [reporting_guideline_check.md](./reporting_guideline_check.md) | clinical, observational, systematic review | EQUATOR Network |
| Manuscript coverage check | [manuscript_coverage_check.md](./manuscript_coverage_check.md) | every full draft | review guidelines |
| Format lint | [format_lint.md](./format_lint.md) | every final output | general best practices |
| HTML editability check | [html_editability_check.md](./html_editability_check.md) | every HTML output | — |
| Response consistency | [response_consistency_check.md](./response_consistency_check.md) | reviewer responses | — |
| Skill structure audit | [skill_structure_check.md](./skill_structure_check.md) | when modifying this skill family | — |

## Integration

Run gates in this order on a final draft:

1. **Manuscript coverage** — all required sections present?
2. **Format lint** — section structure, numbering, references consistent?
3. **Claim / evidence audit** — every major claim supported?
4. **Citation audit** — every citation valid and grounded?
5. **Reproducibility** — methods section sufficient for replication?
6. **Reporting guideline** — domain-specific compliance (CONSORT for RCTs, etc.)?
7. **Reviewer simulation** — pre-submission peer review (only for high-stakes work)?
8. **HTML editability** — output meets the editable-HTML contract?
9. **Response consistency** — only when handling rebuttals.

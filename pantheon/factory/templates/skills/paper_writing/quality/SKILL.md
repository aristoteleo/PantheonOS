---
id: paper_writing_quality
name: Paper Writing Quality Gates
description: |
  Quality gate index for claim/evidence audit, reviewer simulation, reporting
  guidelines, reproducibility, manuscript coverage, and format lint.
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
| Reviewer simulation (NeurIPS-style) | [../writing/reviewer_rubric.md](../writing/reviewer_rubric.md) | submissions, grants, rebuttals | AI-Scientist |
| Reproducibility audit | [reproducibility_check.md](./reproducibility_check.md) | methods, lab reports, workshops | nature-polishing |
| Reporting guideline check | [reporting_guideline_check.md](./reporting_guideline_check.md) | clinical, observational, systematic review | EQUATOR Network |
| Manuscript coverage check | [manuscript_coverage_check.md](./manuscript_coverage_check.md) | every full draft | review guidelines |
| Format lint | [format_lint.md](./format_lint.md) | every final output | general best practices |
| Citation audit | [../evidence/citation_grounding.md](../evidence/citation_grounding.md) | manuscripts with references | nature-citation |
| HTML editability check | [../formats/html_editable_contract.md](../formats/html_editable_contract.md) (Validation section) | every HTML output | Anthropic pdf, Kami |
| Response consistency check | [../scenarios/revision_response.md](../scenarios/revision_response.md) (Response Consistency Check section) | reviewer responses | nature-response, DeepScientist |

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

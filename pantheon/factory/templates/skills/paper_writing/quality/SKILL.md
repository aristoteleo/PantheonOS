---
id: quality_skills_index
name: Quality Skills Index
description: |
  Quality assurance skills for academic writing. Includes reporting guideline
  compliance, reproducibility checks, format validation, and manuscript completeness.
---

# Quality Skills

Tools for ensuring manuscript quality, completeness, and compliance with standards.

## Available Skills

| Skill | File | Purpose | Source |
|-------|------|---------|--------|
| Reporting Guideline Check | [reporting_guideline_check.md](./reporting_guideline_check.md) | Verify CONSORT/STROBE/PRISMA compliance | scientific-writing guidelines |
| Reproducibility Check | [reproducibility_check.md](./reproducibility_check.md) | Ensure methods are reproducible | nature-polishing |
| Format Lint | [format_lint.md](./format_lint.md) | Check formatting (sections, numbering, references) |本地 PDF |
| Manuscript Coverage Check | [manuscript_coverage_check.md](./manuscript_coverage_check.md) | Verify all required sections present | review guidelines |

## When to Use

- **Writer**: After completing draft, before submitting to leader
- **Leader**: During Step 7 (draft review) as quality gate
- **Scenario-specific**: paper_submission scenario requires these checks

## Quality Gate Philosophy

Quality checks are **guardrails, not gatekeepers**:
- Aim for ≥80% compliance, not 100%
- Flag issues, suggest fixes, but don't block progress
- Prioritize critical issues (missing data, unsupported claims) over minor formatting

## Integration

These skills work together:
1. **Format Lint**: Check structure (sections, numbering, references)
2. **Manuscript Coverage**: Check completeness (all required sections present)
3. **Reproducibility**: Check methods section (software, parameters, data)
4. **Reporting Guideline**: Check domain-specific requirements (CONSORT for RCTs, etc.)

---
id: paper_writing_quality
name: Paper Writing Quality Gates
description: |
  Quality gate index for paper-writing tasks: claim/evidence audit, reviewer
  simulation, reporting guidelines, manuscript coverage, format lint, and
  reproducibility. Each gate is either inlined here as a compact checklist
  or pointed to its real home.
tags: [paper_writing, quality, review]
---

# Quality Gates

Quality gates produce reports and risk flags. They are guardrails, not
gatekeepers — flag issues and suggest fixes; do not silently rewrite a draft
to hide problems.

## Philosophy

- Aim for **≥80% compliance**, not 100%.
- Prioritize critical issues (missing data, unsupported claims, fabricated citations) over cosmetic formatting.
- Each gate produces an inspectable report; downstream consumers decide whether to revise, downgrade, or accept the risk.

## Gate Index

| Gate | Where | Default trigger |
|---|---|---|
| Claim / evidence + citation grounding | [../writing/claim_evidence_check.md](../writing/claim_evidence_check.md) | every full draft, every paper with citations |
| Reviewer simulation (NeurIPS-style) | [../writing/reviewer_rubric.md](../writing/reviewer_rubric.md) | submissions, grants, rebuttals |
| Reporting guideline check | [reporting_guideline_check.md](./reporting_guideline_check.md) | clinical, observational, systematic review |
| Manuscript coverage | inlined below | every full draft |
| Format lint | inlined below | every final output |
| Reproducibility | inlined below; see also [../writing/method.md](../writing/method.md) | methods, lab reports, computational papers |
| HTML editability | [../formats/html_editable_contract.md](../formats/html_editable_contract.md) (Validation section) | every HTML output |
| Response consistency | [../scenarios/revision_response.md](../scenarios/revision_response.md) (Response Consistency Check section) | reviewer responses |

## Run Order on a Final Draft

1. **Manuscript coverage** — all required sections present?
2. **Format lint** — section structure, numbering, references consistent?
3. **Claim / evidence + citation grounding** — every major claim supported, every citation aligned?
4. **Reproducibility** — methods sufficient for replication?
5. **Reporting guideline** — domain-specific compliance (CONSORT for RCTs, etc.)?
6. **Reviewer simulation** — pre-submission peer review (only for high-stakes work)?
7. **HTML editability** — output meets the editable-HTML contract?
8. **Response consistency** — only when handling rebuttals.

---

## Manuscript Coverage Check

Verify required sections are present and complete.

### Core sections (required for most papers)

- Title (descriptive, specific, <150 chars)
- Abstract (150-300 words)
- Introduction (background, gap, contribution)
- Methods (sufficient detail for reproduction)
- Results (findings with figures/tables)
- Discussion (interpretation, limitations, future work)
- References

### Optional sections (journal-dependent)

- Keywords, Conclusion, Acknowledgements, Author Contributions, Competing Interests, Data Availability, Code Availability, Supplementary Materials.

### Per-section completeness checklist

| Section | Must contain |
|---|---|
| Abstract | background → gap → method → results → conclusion |
| Introduction | background → related work → gap → contribution |
| Methods | design, materials, procedure, parameters, analysis |
| Results | main findings, figures, tables, statistics, subsections by question |
| Discussion | interpretation, comparison, limitations, future, take-home |

### Output

```markdown
## Manuscript Coverage Report
**Completeness**: 85% (17/20 sections)
### Present and Complete (17): list with one-line confirmation each.
### Missing or Incomplete (3):
1. <Section> — Status / Action / Location
2. ...
### Recommendation: which gaps to fix before next phase.
```

### Gate

100% / ≥90% excellent / 80-89% good / <80% major sections missing.

---

## Format Lint

Validate manuscript formatting against common journal requirements.

### Checklist

**Section structure**: title, abstract, keywords, intro, methods, results, discussion, conclusion, references, ack, author contributions, competing interests — each present if required.

**Figure / table numbering**: sequential (no gaps), every one referenced in text, every one has a caption, captions follow journal style.

**References**: numbered sequentially, no gaps, every list entry cited in text, every text citation in list, consistent format, DOIs where required.

**Word / page limits**: abstract, main text, figures, references all within journal limits.

**Supplementary**: every supplementary file referenced, numbered (S1, S2, ...), briefly described.

### Output

```markdown
## Format Lint Report
**Compliance**: 90% (27/30 items)
### Correct: list categories.
### Issues Found:
1. <Severity> <Issue>: location + action.
### Word Count: section vs limit (✅/❌).
### Recommendation
```

### Common journal limits (quick reference)

| Type | Abstract | Main text | Figures | Refs |
|---|---|---|---|---|
| Nature/Science | 150-200 | 3-4k words | 4-6 | 30-50 |
| PLOS ONE | 300 | none | none | none |
| Cell | 150 | 5k | 7 | 80 |
| NeurIPS | 250 | 8 pages | none | none |
| JMLR | 200 | none | none | none |

Always check the specific journal's author guidelines.

### Gate

100% perfect / ≥90% excellent / 80-89% fix flagged / <80% major problems.

---

## Reproducibility Check

Ensure methods provide sufficient detail for independent reproduction.

The drafting checklist for reproducibility lives in [../writing/method.md](../writing/method.md) ("Reproducibility Checklist" section). Use this gate to **audit** a finished draft against that checklist; do not duplicate the rules here.

### Audit categories (each must be present and concrete)

1. **Software & tools** — names, versions, OS, language, key libraries.
2. **Parameters & settings** — hyperparameters, random seeds, hardware, training details, preprocessing.
3. **Data** — source, version, sample size, splits, inclusion/exclusion, availability.
4. **Code** — repository URL, license, dependencies (`requirements.txt` / `environment.yml`), reproducibility script.

### Output

```markdown
## Reproducibility Check Report
**Compliance**: 75% (15/20 items)
### Sufficient Detail (15): list verified items.
### Missing or Vague (5):
1. <Severity> <Item>
   - Current: <text from draft>
   - Fix: <concrete replacement>
### Data / Code Availability Statements
- Public / Restricted / New data — pick one template, fill in.
### Recommendation
```

### Gate

≥90% excellent / 80-89% good / 70-79% fair / <70% major revision.

### Availability statement templates

- **Public data**: "<Dataset> publicly available at <URL>. Preprocessed data and trained models at <repo>."
- **Restricted data**: "Patient data cannot be shared due to privacy. Aggregated statistics and analysis code at <repo>."
- **New data**: "All data generated in this study available at Zenodo (DOI: ...) under <license>."
- **Open code**: "All code at <URL> under <license>. Installation in README."
- **Proprietary**: "Code available for research purposes upon reasonable request to corresponding author."

---

Sources: review/SKILL.md (manuscript coverage), local PDF (format lint
practices), nature-polishing reproducibility, EQUATOR Network (reporting
guidelines).

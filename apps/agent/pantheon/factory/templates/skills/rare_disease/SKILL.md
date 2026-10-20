---
id: rare_disease_skills_index
name: Rare Disease Skills Index
description: |
  Skills for rare disease case support: ontology-first normalization/retrieval
  and the clinical genetics consult report format contract (structure + theme).
  Load the relevant skill file when performing the matching task.
tags: [rare_disease, ontology, orphanet, omim, hpo, clinical_report]
---

# Agent Skills for Rare Disease Case Support

Curated playbooks for the rare disease MDT workflow. Each agent loads the skill
file that matches its current task — do not inline these contracts into prompts.

## Ontology-First Workflow

Deterministic normalization against the local Orphanet/OMIM/HPO layer before any
online evidence retrieval. Disease alias normalization, phenotype alignment,
xref resolution, and the SQLite query scripts live here.

**Skill file**: [rd_ontology_first.md](./rd_ontology_first.md)

**When to use**:
- Normalizing free-text phenotype or disease aliases (researcher, `ontology` mode)
- Resolving ORPHA/OMIM/MONDO/HPO cross-IDs before retrieval (researcher, `ontology` mode)
- Any candidate-name standardization step

---

## Clinical Report Format

The authoritative format contract for the final clinical genetics consult report:
cover page, 4-level numbering, 9 sections, sign-off block, machine-readable JSON,
and the standalone HTML theme (embedded CSS, weasyprint PDF path).

**Skill file**: [clinical_report_format.md](./clinical_report_format.md)

**When to use**:
- Producing the final report (leader, after audit)
- Any time a deliverable must follow the signed-report structure and theme

**IMPORTANT**: This file is the single source of truth for report layout and
styling. The leader must follow it exactly rather than improvising structure
or CSS.

---

## Guardrails (apply across all rare disease skills)

- Ontology normalization comes before retrieval, never after.
- Do not fabricate xref IDs, PMIDs, DOIs, or database annotations.
- Do not present online evidence as ontology truth.
- The report is decision-support, never a definitive diagnosis.

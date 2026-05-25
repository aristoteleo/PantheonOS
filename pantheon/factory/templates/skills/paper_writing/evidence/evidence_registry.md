---
id: paper_writing_evidence_registry
name: Evidence Registry
description: Claim-evidence map protocol for binding every core claim to citation, figure, table, data, user material, or missing evidence.
tags: [paper_writing, evidence, claims]
---

# Evidence Registry

Use before and after drafting. The registry is the source of truth for what the
paper may claim.

## Output

Create `claim_evidence_map.md`:

| Claim ID | Claim | Evidence type | Source | Strength | Risk | Action |
|---|---|---|---|---|---|---|
| C1 | ... | citation/figure/table/data/user_material/missing | S001 | strong | low | keep |

## Rules

- Evidence types are only `citation`, `figure`, `table`, `experimental_data`,
  `statistical_result`, `user_material`, or `missing`.
- Claims with `missing` evidence cannot appear as firm conclusions.
- If support is partial, narrow the wording.

Sources: DeepScientist paper-outline/SKILL.md, paper-review.md.

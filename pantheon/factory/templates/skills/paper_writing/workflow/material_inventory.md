---
id: paper_writing_material_inventory
name: Material Inventory
description: Inventory protocol for mapping user files, notes, data, figures, drafts, and reviewer comments to paper sections and evidence gaps.
tags: [paper_writing, materials, inventory]
---

# Material Inventory

Use after triage when the user has supplied files, notes, datasets, figures, or
reviewer comments.

## Output

Write `materials/inventory.md` with this table:

| Material ID | Path/source | Type | Relevant sections | Claims supported | Gaps/risks |
|---|---|---|---|---|---|
| M001 | user file or note | draft/data/figure/PDF/comment | Methods, Results | C1, C4 | missing sample size |

## Rules

- Do not leave attachments only in chat context. Give each useful item an ID.
- Mark unreadable or missing files explicitly.
- Separate user-provided facts from model inference.
- Map reviewer comments to `revision_response` IDs, not generic notes.

Sources: Anthropic doc-coauthoring context gathering, local design PDF.

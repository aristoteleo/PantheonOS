---
id: paper_writing_triage
name: Paper Writing Triage
description: Triage protocol for selecting scenario, format, theme, language, audience, outputs, constraints, and quality gates.
tags: [paper_writing, triage]
---

# Triage Protocol

Use at the start of every paper-writing task and whenever user intent changes.

## Output

Create or update `triage.md`:

```yaml
scenario_id: paper_submission
format_id: journal_article
theme_id: editable_article
language: zh
audience: reviewers
source_materials:
  - materials/inventory.md
output:
  markdown_source: draft/paper.md
  html_path: report/paper_preview.html
  editable_html: true
  pdf: true
  docx: false
  latex: false
constraints:
  venue_profile: null
  page_size: A4
  word_limits: null
quality_gates:
  - claim_evidence_check
  - reviewer_rubric
  - html_editability_check
```

## Checks

- If a UI label and user text conflict, prefer the user's latest text and note
  the conflict in `constraints`.
- If target venue is unknown, keep `format_id` generic but do not ignore any
  provided page/word/style limits.
- If the user only asks for a small section edit, still record scenario and
  gates, but output only the requested section plus relevant quality notes.

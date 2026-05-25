---
id: paper_writing_html_editability_check
name: HTML Editability Check
description: Validate standalone editable HTML output, semantic blocks, print CSS, and data attributes.
tags: [paper_writing, html, quality]
---

# HTML Editability Check

Use after every HTML generation.

## Pass Criteria

- Single HTML file opens without the app runtime.
- CSS is embedded.
- Main text is semantic HTML, not an image.
- Major blocks have `contenteditable="true"`.
- Major blocks include `data-block-id`, `data-section`, `data-source`, and
  `data-format-role`.
- Print CSS includes `@page` or `@media print`.
- Figures/tables use semantic `figure`, `figcaption`, `table`, `caption` where
  applicable.

Sources: Anthropic pdf/SKILL.md, Kami README/design/CHEATSHEET, local design PDF.

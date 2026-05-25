---
id: paper_writing_html_editable_contract
name: Editable HTML Contract
description: Contract for standalone, semantic, editable HTML outputs with inline CSS, print CSS, and data block attributes.
tags: [paper_writing, html, editable]
---

# Editable HTML Contract

All paper-writing HTML outputs must be standalone working files, not app-only
previews or screenshots.

## Required Block Shape

```html
<section
  class="editable-block"
  data-block-id="abstract-001"
  data-section="abstract"
  data-source="draft/paper.md"
  data-format-role="summary"
  contenteditable="true">
  <h2>Abstract</h2>
  <p>...</p>
</section>
```

## Required Properties

- Single file with embedded CSS.
- Main content remains text HTML.
- Use semantic tags: `section`, `figure`, `figcaption`, `table`, `caption`,
  `ol`, `ul`, `aside` where appropriate.
- Include `@page` or `@media print`.
- Do not require frontend runtime to display the report body.
- Avoid absolute positioning for body layout.

## Block Attributes

| Attribute | Meaning |
|---|---|
| `data-block-id` | stable local block identifier |
| `data-section` | manuscript/report section |
| `data-source` | source file, usually `draft/paper.md` |
| `data-format-role` | role such as `summary`, `claim`, `evidence`, `method`, `response` |

## Validation (Pass Criteria)

Run after every HTML generation:

- Single HTML file opens without the host application's runtime.
- CSS is embedded.
- Main text is semantic HTML, not an image.
- Major blocks have `contenteditable="true"`.
- Major blocks include `data-block-id`, `data-section`, `data-source`, and
  `data-format-role`.
- Print CSS includes `@page` or `@media print`.
- Figures and tables use semantic `figure`, `figcaption`, `table`, `caption`
  where applicable.

If any pass criterion fails, fix the renderer or the source Markdown rather
than relaxing the contract.

Sources: Anthropic pdf/SKILL.md, Kami design.md/CHEATSHEET.md, local design PDF.

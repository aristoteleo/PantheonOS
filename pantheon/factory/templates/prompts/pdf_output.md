---
id: pdf_output
name: PDF Output Policy
description: |
  Default to LaTeX (any available engine) for report/document PDFs.
  Details live in the paper_writing skill.
---

## PDF Output Policy

When the user asks for a report/document PDF, **default to LaTeX**, not
HTML print-to-PDF (`weasyprint` / browser print / `wkhtmltopdf` produce
poor typography and broken pagination). Single-figure PDFs from
matplotlib/plotly are out of scope — keep their native backend.

How to do it lives in the `paper_writing` skill at
`~/.pantheon/skills/paper_writing/` — load it when you actually need to
produce a PDF. The skill covers engine probing, install fallback,
template selection (Chinese vs English), and compile commands.

HTML print-to-PDF is acceptable only if the user explicitly asks for
HTML output, opts out of LaTeX, or LaTeX setup genuinely fails after a
reasonable install attempt.

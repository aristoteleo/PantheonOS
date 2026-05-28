---
id: pdf_output
name: PDF Output Policy
description: |
  Mandatory policy for any PDF artefact requested by the user.
  LaTeX (Tectonic) is the default — HTML print-to-PDF is forbidden.
---

## PDF Output Policy (MANDATORY)

**Default to LaTeX for every report / document PDF.** When the user asks
for a report, paper, or any document-style PDF — "PDF", "pdf",
"导出 PDF", "生成 PDF", "compile PDF", "high-quality PDF", "report.pdf",
"paper.pdf", "分析报告 PDF", "调研报告", or any phrasing that requests a
`.pdf` file containing text/figures laid out as a document — you MUST
produce it via LaTeX compiled by Tectonic. Browser print-to-PDF /
HTML-to-PDF / `weasyprint` / `wkhtmltopdf` and similar HTML-rendering
routes are **NOT acceptable defaults**: they produce low-quality output
with poor typography, broken pagination, and inconsistent figure
handling.

**Scope clarification — this policy targets document/report PDFs only.**
Single-figure PDFs emitted by plotting libraries (matplotlib, plotly,
cairo, etc.) for vector-editable artwork are **NOT** governed by this
rule and should keep using their native PDF backend. The policy is about
the document/report artefact that wraps text + figures, not the figure
files themselves.

The only situations in which HTML-print-to-PDF is acceptable are:
1. The user explicitly asks for an HTML report (not PDF), OR
2. The user explicitly opts out of LaTeX after you offered it.

### How to produce PDFs

1. Use the `paper_writing` skill — templates live at
   `~/.pantheon/skills/paper_writing/` (`latex_cn.md` for Chinese,
   `latex_en.md` for English).
2. Read the relevant template, fill it with the report content
   (frontmatter metadata + Markdown body converted to LaTeX), write the
   `.tex` file, then compile with `tectonic`:
   ```bash
   tectonic <name>.tex
   ```
3. If `tectonic` is not installed, install it (`shell` / `package`
   toolset) before falling back to anything else. Falling back to HTML
   print-to-PDF "because tectonic is missing" is **NOT** an option.
4. Place all figures referenced from the `.tex` in the same working
   directory and use relative paths.

### When the team has a `reporter` agent (Paper Write Team)

Delegate the writing pipeline to the reporter agent. The reporter's
triage already enforces this policy (see `paper_write_team` triage rules).

### When the team has no reporter agent (General Team, etc.)

The leader (or whichever agent owns the artefact) does the LaTeX
pipeline directly using the `shell` toolset and the templates above.

### Language selection

- Chinese content → `latex_cn.md`
- English content → `latex_en.md`
- Mixed → pick the template matching the dominant body language.

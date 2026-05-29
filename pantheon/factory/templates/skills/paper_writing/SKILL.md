---
id: paper_writing_skills_index
name: Paper Writing Skills Index
description: |
  Skills for the Paper Write Team: report and academic templates for
  HTML/PDF rendering. Each template file is self-contained (HTML + CSS
  or LaTeX in a single markdown file).
---

# Paper Writing Skills

Resources for the Paper Write Team's reporter agent. Each template is a
self-contained markdown file with the full HTML+CSS or LaTeX content.

## Templates

| Template | File | Style | Use Case |
|----------|------|-------|----------|
| `report_standard` | [report_standard.md](./report_standard.md) | Professional report (Manus-style), HTML+CSS | Default for all reports |
| `report_academic` | [report_academic.md](./report_academic.md) | Formal academic paper, HTML+CSS | HTML preview for academic papers |
| `latex_cn` | [latex_cn.md](./latex_cn.md) | Chinese academic paper, LaTeX | Chinese academic PDF (XeTeX-family) |
| `latex_en` | [latex_en.md](./latex_en.md) | English academic paper, LaTeX | English academic PDF (XeTeX-family) |

## Compile playbook

Engine probing, install fallback, and compile commands live in
[COMPILE.md](./COMPILE.md). Read it before compiling a `.tex` — do not
hard-code Tectonic; probe what's available first.

## How to Use

### Report style (default)

1. Reporter reads this skill index
2. Reporter reads `report_standard.md` — contains both the HTML template and CSS
3. Reporter reads paper.md, parses frontmatter, converts Markdown body to HTML
4. Reporter fills the HTML template with metadata + CSS + content
5. Reporter writes the final HTML file
6. The UI exports the HTML to PDF on user request (browser print-to-PDF using the `@media print` CSS rules)

### Academic style

1. Reporter reads this skill index
2. Reporter reads the LaTeX template (`latex_cn.md` or `latex_en.md` based on lang)
3. Reporter reads paper.md, parses frontmatter, converts Markdown body to LaTeX
4. Reporter fills the LaTeX template with metadata + content, writes .tex file
5. Reporter runs Tectonic to compile PDF
6. Reporter also reads `report_academic.md` to generate an HTML preview

### Custom templates

Users can add their own `.md` template files to this directory following the
same format (frontmatter + HTML/CSS or LaTeX in code blocks).

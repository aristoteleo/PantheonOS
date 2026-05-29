---
id: report_academic
name: Report Academic Template
description: |
  Formal academic paper template. Contains HTML template with embedded CSS.
  Serif fonts (Computer Modern), paragraph indent, booktabs tables, theorem
  environments, auto-numbered sections/figures/tables.
---

# Report Academic Template

Formal academic paper style with LaTeX-like appearance for HTML preview.

## HTML Template

Reporter reads paper.md, converts Markdown to HTML, then fills this template.
Use `paper-` class prefix for all metadata elements.

```html
<!DOCTYPE html>
<html lang="${{LANG}}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${{TITLE}}</title>
  <script>
    window.MathJax = {
      tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\[', '\\]']] },
      options: { skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }
    };
  </script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
  <style>
${{CSS}}
  </style>
</head>
<body>
  <article class="paper">
    <header class="paper-header">
      <h1 class="paper-title">${{TITLE}}</h1>
      ${{AUTHORS_BLOCK}}
      ${{DATE_BLOCK}}
    </header>
    <main class="paper-body">
${{CONTENT}}
    </main>
  </article>
</body>
</html>
```

### Authors block format (use `paper-` prefix)

```html
<div class="paper-authors">
  <span class="author">Name 1<sup>1</sup></span>
  <span class="author">Name 2<sup>2</sup></span>
</div>
<div class="paper-affiliations">
  <div><sup>1</sup> Affiliation 1</div>
</div>
```

### Notes

- CSS auto-numbers sections (h2, h3, h4), figures, and tables — writer should NOT manually number them
- Theorem environments use fenced divs: `<div class="theorem">...</div>`
- Figure captions get auto-prefixed with "Figure N:" — writer should NOT include the prefix

## CSS

```css
@import url('https://cdn.jsdelivr.net/gh/aaaakshat/cm-web-fonts@latest/fonts.css');

body {
    font-family: 'Computer Modern Serif', 'CMU Serif', 'Noto Serif',
                 'Songti SC', 'SimSun', Georgia, 'Times New Roman', serif;
    max-width: 680px; margin: 48px auto; padding: 0 20px;
    line-height: 1.5; color: #000; background: #fff; font-size: 11pt;
    text-rendering: optimizeLegibility; -webkit-font-smoothing: antialiased;
}

.paper-header { text-align: center; margin-bottom: 28px; }
.paper-title { font-size: 17pt; font-weight: 700; color: #000; margin-bottom: 12px; line-height: 1.25; }
.paper-authors { font-size: 12pt; color: #000; margin-bottom: 4px; }
.paper-authors .author { margin-right: 12px; }
.paper-affiliations { font-size: 10pt; color: #333; font-style: italic; }
.paper-date { font-size: 10pt; color: #555; margin-top: 8px; }

.abstract, section.abstract { margin: 24px 48px; font-size: 10pt; text-align: justify; }
section.abstract > h2 { font-size: 11pt; font-weight: 700; text-align: center; border: none; padding: 0; margin: 0 0 6px 0; }

body { counter-reset: h2 h3 h4 fig tbl eq thm lem def; }
h2 { counter-increment: h2; counter-reset: h3 h4; }
h2::before { content: counter(h2) "  "; }
h3 { counter-increment: h3; counter-reset: h4; }
h3::before { content: counter(h2) "." counter(h3) "  "; }
h4 { counter-increment: h4; }
h4::before { content: counter(h2) "." counter(h3) "." counter(h4) "  "; }

h2 { font-size: 14pt; font-weight: 700; color: #000; margin-top: 28px; margin-bottom: 10px; }
h3 { font-size: 12pt; font-weight: 700; color: #000; margin-top: 20px; margin-bottom: 8px; }
h4 { font-size: 11pt; font-weight: 700; font-style: italic; color: #000; margin-top: 16px; margin-bottom: 6px; }

p { margin-bottom: 0; margin-top: 0; text-align: justify; text-indent: 1.5em; hyphens: auto; }
h2 + p, h3 + p, h4 + p, figure + p, table + p, ul + p, ol + p, blockquote + p, .abstract + p, .theorem + p, .proof + p { text-indent: 0; }

figure { counter-increment: fig; text-align: center; margin: 20px auto; max-width: 90%; }
figure img { max-width: 100%; height: auto; }
figcaption { font-size: 10pt; margin-top: 8px; text-align: justify; }
figcaption::before { content: "Figure " counter(fig) ": "; font-weight: 700; }

table { counter-increment: tbl; width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 10pt; }
caption { font-size: 10pt; text-align: left; margin-bottom: 6px; caption-side: top; }
caption::before { content: "Table " counter(tbl) ": "; font-weight: 700; }
thead { border-top: 1.5pt solid #000; border-bottom: 0.75pt solid #000; }
tbody { border-bottom: 1.5pt solid #000; }
th, td { padding: 4px 10px; text-align: left; }
th { font-weight: 600; }

.MathJax_Display, mjx-container[display="true"] { margin: 14px 0; text-align: center; }

code { font-family: 'Computer Modern Typewriter', 'CMU Typewriter Text', 'Courier New', monospace; font-size: 0.92em; background: #f5f5f5; padding: 1px 4px; }
pre { background: #f9f9f9; padding: 12px; border: 0.5pt solid #ddd; overflow-x: auto; font-size: 9.5pt; }
pre code { background: none; padding: 0; }

.theorem, .lemma, .definition, .proposition, .corollary, .remark { margin: 14px 0; padding: 0; }
.theorem { counter-increment: thm; font-style: italic; }
.theorem::before { content: "Theorem " counter(thm) ". "; font-weight: 700; font-style: normal; }
.lemma { counter-increment: lem; font-style: italic; }
.lemma::before { content: "Lemma " counter(lem) ". "; font-weight: 700; font-style: normal; }
.definition { counter-increment: def; }
.definition::before { content: "Definition " counter(def) ". "; font-weight: 700; font-style: normal; }
.proof { margin: 6px 0 14px 0; }
.proof::before { content: "Proof. "; font-style: italic; }
.proof::after { content: " □"; float: right; font-style: normal; }

.references, #refs { font-size: 9.5pt; }
.csl-entry { margin-bottom: 6px; text-indent: -1.5em; padding-left: 1.5em; }
.footnotes { font-size: 9pt; border-top: 0.5pt solid #000; margin-top: 32px; padding-top: 12px; }
blockquote { margin: 12px 24px; padding: 0; font-size: 10.5pt; color: #333; border-left: none; font-style: italic; }
ul, ol { margin: 8px 0 8px 24px; }
li { margin-bottom: 3px; }
a { color: #000; text-decoration: none; border-bottom: 0.5pt dotted #666; }
a:hover { border-bottom-style: solid; }
hr { border: none; border-top: 0.5pt solid #ccc; margin: 24px 0; }

@media print {
    body { max-width: none; margin: 0; padding: 24px 48px; }
    @page { size: A4; margin: 2.5cm; }
    @page :first { margin-top: 3cm; }
    h2 { page-break-after: avoid; }
    figure, table { page-break-inside: avoid; }
    a { border-bottom: none; color: #000; }
}

@media (max-width: 768px) {
    body { padding: 0 16px; font-size: 10.5pt; }
    .abstract, section.abstract { margin: 16px 0; }
    p { text-indent: 1em; }
}
```

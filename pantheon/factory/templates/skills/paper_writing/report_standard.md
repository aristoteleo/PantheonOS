---
id: report_standard
name: Report Standard Template
description: |
  Professional report template (Manus-style). Contains HTML template with
  embedded CSS. Sans-serif, bold headings with dividers, generous whitespace.
  Used as the default for all report-style outputs.
---

# Report Standard Template

Clean professional report style targeting Manus/AI4S report quality.

## HTML Template

Reporter reads paper.md, converts Markdown to HTML, then fills this template.
Replace `${{PLACEHOLDER}}` markers with actual content.

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
  <article class="report">
    <header class="report-header">
      <h1 class="report-title">${{TITLE}}</h1>
      ${{AUTHORS_BLOCK}}
      ${{DATE_BLOCK}}
    </header>
    <main class="report-body">
${{CONTENT}}
    </main>
  </article>
</body>
</html>
```

### Placeholders

| Placeholder | Value |
|-------------|-------|
| `${{LANG}}` | Language from frontmatter (`zh` or `en`) |
| `${{TITLE}}` | Title from frontmatter |
| `${{CSS}}` | The full CSS below, embedded inline |
| `${{AUTHORS_BLOCK}}` | Authors HTML (see below) or empty string |
| `${{DATE_BLOCK}}` | `<div class="report-date">2026-04-29</div>` or empty string |
| `${{CONTENT}}` | Converted HTML body |

### Authors block format

```html
<div class="report-authors">
  <span class="author">Name 1<sup>1</sup></span>
  <span class="author">Name 2<sup>2</sup></span>
</div>
<div class="report-affiliations">
  <div><sup>1</sup> Affiliation 1</div>
  <div><sup>2</sup> Affiliation 2</div>
</div>
```

### Semantic wrappers

When converting Markdown to HTML, apply these wrappers for proper styling:

- **Abstract**: Wrap the first section ("摘要" or "Abstract") in `<section class="abstract">...</section>`
- **References**: Wrap the last section ("参考文献" or "References") in `<section class="references">...</section>`
- **Images**: Convert `![caption](path)` to `<figure><img src="path"><figcaption>caption</figcaption></figure>`

## CSS

```css
/*
 * Report Standard Theme
 * Clean professional report style targeting Manus/AI4S report quality.
 * Sans-serif, bold headings with dividers, generous whitespace.
 */

/* ===== Base ===== */
body {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei",
                 "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 60px 40px 80px;
    line-height: 1.8;
    color: #333;
    background: #fff;
    font-size: 16px;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
}

/* ===== Title block ===== */
.report-header {
    margin-bottom: 40px;
    padding-bottom: 20px;
}

.report-title {
    font-size: 32px;
    font-weight: 800;
    color: #000;
    line-height: 1.3;
    margin: 0 0 16px 0;
    letter-spacing: -0.02em;
}

.report-authors { font-size: 15px; color: #555; margin-bottom: 4px; }
.report-authors .author { margin-right: 16px; }
.report-affiliations { font-size: 13px; color: #777; margin-bottom: 8px; }
.report-date { font-size: 14px; color: #999; }

/* ===== Abstract ===== */
section.abstract, div.abstract { margin-bottom: 32px; }
section.abstract > h2, .report-body > h2:first-child {
    font-size: 18px; font-weight: 700; color: #000;
    margin-bottom: 12px; border-bottom: none; padding-bottom: 0; margin-top: 0;
}

/* ===== Headings ===== */
h2 {
    font-size: 24px; font-weight: 700; color: #000;
    margin-top: 48px; margin-bottom: 16px;
    padding-bottom: 10px; border-bottom: 2px solid #333; line-height: 1.3;
}
h3 { font-size: 20px; font-weight: 700; color: #000; margin-top: 36px; margin-bottom: 12px; line-height: 1.3; }
h4 { font-size: 17px; font-weight: 600; color: #222; margin-top: 28px; margin-bottom: 10px; }

/* ===== Paragraphs ===== */
p { margin-bottom: 16px; text-align: justify; word-break: break-word; }

/* ===== Lists ===== */
ul, ol { margin: 16px 0; padding-left: 28px; }
li { margin-bottom: 10px; line-height: 1.7; }
li > strong:first-child { color: #000; }
li > ul, li > ol { margin-top: 8px; margin-bottom: 8px; }

/* ===== Citations [1] ===== */
sup { font-size: 0.75em; line-height: 0; position: relative; vertical-align: baseline; top: -0.4em; }
a.citation-ref { color: #2563eb; text-decoration: none; font-weight: 500; }
a.citation-ref:hover { text-decoration: underline; }

/* ===== Figures ===== */
figure, .figure { text-align: center; margin: 32px 0; page-break-inside: avoid; }
figure img, .figure img { max-width: 90%; height: auto; border-radius: 2px; }
figcaption, .figure-caption { font-size: 14px; color: #555; margin-top: 12px; text-align: center; line-height: 1.5; }
figcaption strong, .figure-caption strong { color: #333; }

/* ===== Tables ===== */
table { width: 100%; border-collapse: collapse; margin: 28px 0; font-size: 15px; page-break-inside: avoid; }
caption { font-size: 14px; color: #555; text-align: left; margin-bottom: 8px; caption-side: top; }
caption strong { color: #333; }
thead { border-top: 2px solid #333; border-bottom: 1px solid #333; }
tbody { border-bottom: 2px solid #333; }
th, td { padding: 10px 14px; text-align: left; line-height: 1.5; }
th { font-weight: 600; color: #000; }

/* ===== Code ===== */
code { font-family: "SF Mono", "Fira Code", Menlo, Consolas, monospace; font-size: 0.9em; background: #f3f4f6; padding: 2px 6px; border-radius: 3px; color: #1f2937; }
pre { background: #f8f9fa; padding: 20px; border-radius: 6px; overflow-x: auto; font-size: 14px; line-height: 1.6; margin: 24px 0; border: 1px solid #e5e7eb; }
pre code { background: none; padding: 0; border-radius: 0; }

/* ===== Blockquotes ===== */
blockquote { margin: 24px 0; padding: 16px 24px; border-left: 4px solid #d1d5db; background: #f9fafb; color: #4b5563; font-style: italic; }
blockquote p:last-child { margin-bottom: 0; }

/* ===== Math (MathJax) ===== */
.MathJax_Display, mjx-container[display="true"] { margin: 20px 0 !important; overflow-x: auto; }

/* ===== References section ===== */
.references, section.references { margin-top: 48px; padding-top: 24px; border-top: 2px solid #333; }
.references h2 { border-bottom: none; padding-bottom: 0; margin-top: 0; }
.reference-list, .references ol { font-size: 14px; line-height: 1.7; color: #555; padding-left: 28px; }
.reference-list li, .references ol li { margin-bottom: 8px; }

/* ===== Misc ===== */
hr { border: none; border-top: 1px solid #e5e7eb; margin: 40px 0; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; height: auto; }
.footnotes { font-size: 13px; color: #666; border-top: 1px solid #e5e7eb; margin-top: 48px; padding-top: 16px; }

/* ===== Print / PDF (browser print or UI export) ===== */
@media print {
    body { max-width: none; margin: 0; padding: 0; font-size: 11pt; line-height: 1.7; color: #000; }
    @page { size: A4; margin: 2.5cm; }
    @page :first { margin-top: 3cm; }
    .report-title { font-size: 26pt; }
    h2 { font-size: 16pt; page-break-after: avoid; margin-top: 28pt; }
    h3 { font-size: 13pt; page-break-after: avoid; }
    h4 { font-size: 11pt; page-break-after: avoid; }
    figure, table { page-break-inside: avoid; }
    pre { page-break-inside: avoid; white-space: pre-wrap; word-wrap: break-word; }
    a { color: #000; text-decoration: none; }
    .references, section.references { page-break-before: auto; }
}

/* ===== Responsive ===== */
@media (max-width: 768px) {
    body { padding: 24px 16px 40px; font-size: 15px; }
    .report-title { font-size: 24px; }
    h2 { font-size: 20px; }
    h3 { font-size: 18px; }
    table { font-size: 13px; }
    th, td { padding: 6px 8px; }
}
```

---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: "#0a0e17"
color: "#e2e8f0"
html: true
style: |
  section {
    font-family: 'Inter', -apple-system, system-ui, sans-serif;
    font-size: 24px;
    padding: 56px 72px;
  }
  h1 { color: #60a5fa; font-weight: 700; }
  h2 { color: #a78bfa; border-bottom: 1px solid #1e293b; padding-bottom: 8px; }
  h3 { color: #34d399; }
  strong { color: #fbbf24; }
  code {
    background: #1e293b;
    color: #93c5fd;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.85em;
  }
  pre {
    background: #0f172a !important;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 14px;
    font-size: 0.78em;
  }
  pre code { background: transparent; padding: 0; }
  table {
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 0.86em;
    background: transparent !important;
    width: 100%;
  }
  th, td {
    border-bottom: 1px solid #1e293b !important;
    padding: 8px 14px;
    text-align: left;
    background: transparent !important;
    color: #e2e8f0 !important;
  }
  th {
    color: #94a3b8 !important;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.78em;
    letter-spacing: 0.05em;
  }
  /* Kill the default theme's zebra striping — it turns rows white on a
     dark background. */
  tbody tr:nth-child(even),
  tbody tr:nth-child(odd) { background: transparent !important; }
  blockquote {
    border-left: 3px solid #60a5fa;
    padding-left: 16px;
    color: #94a3b8;
    font-style: italic;
  }
  ul, ol { line-height: 1.6; }
  li { margin-bottom: 4px; }
  /* Mermaid container. Bound the SVG so it never overflows the slide. */
  .mermaid {
    text-align: center;
    background: transparent;
    padding: 4px;
  }
  .mermaid svg {
    max-height: 60vh !important;
    height: auto !important;
    width: auto !important;
    max-width: 100% !important;
  }
  /* Inline span colours used in the body. */
  .blue { color: #60a5fa; }
  .purple { color: #a78bfa; }
  .green { color: #34d399; }
  .yellow { color: #fbbf24; }
  /* Lead (cover) slide. */
  section.lead {
    text-align: center;
    justify-content: center;
  }
  section.lead h1 { font-size: 2.6em; }
  section.lead h3 { color: #94a3b8; font-weight: 400; margin-top: 0.6em; }
---

<!--
This deck was authored with the marp-slides skill.
For mermaid diagrams to render, open the exported HTML via a local
http server (e.g. `python3 -m http.server 8765`) — file:// origin
blocks the CDN script.
-->

<!-- _class: lead -->

# Deck Title

### Subtitle / one-line framing

---

## Section heading

- Bullet idea 1
- Bullet idea 2 with **emphasis**
- Bullet 3 referencing `inline_code`

> Pull quote or callout

---

## Two-column layout via table (1:1)

| Left column                                | Right column                            |
| ------------------------------------------ | --------------------------------------- |
| First half of the comparison.              | Second half.                            |
| Another row.                               | Aligned.                                |

---

## Mermaid diagram example

<div class="mermaid">flowchart LR; A[User] --> B[API] --> C[(Database)]; B --> D[Background\nworker]; D --> E[Result]</div>

---

## Code fragment

```python
def evaluate(candidate: Path) -> dict:
    """Evaluator runs the candidate, returns scored metrics."""
    return run_eval(candidate)
```

---

<!-- _class: lead -->

# Thank you

### Questions?

<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: "dark", themeVariables: { darkMode: true, background: "#0a0e17", primaryColor: "#1e293b", primaryTextColor: "#e2e8f0", primaryBorderColor: "#475569", lineColor: "#64748b" } });</script>

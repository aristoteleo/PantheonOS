---
id: paper_writing_kami_academic_theme
name: Kami Academic Theme
description: Warm parchment, ink-blue, serif-hierarchy theme constraints for editable academic HTML.
tags: [paper_writing, kami, html_theme]
---

# Kami Academic Theme

Use for editable academic HTML when the user wants a refined, print-friendly
document.

| Element | Rule |
|---|---|
| Canvas | parchment `#f5f4ed`; avoid pure white page background |
| Accent | ink blue `#1B365D`; small surface area |
| Neutrals | warm gray/stone, not cool blue-gray |
| Typography | Chinese serif fallback first, English Georgia/Charter-style serif |
| Headings | serif, moderate weight, compact hierarchy |
| Tables | light borders, dense rows, readable print spacing |
| Tags | solid hex colors, no translucent decorative blobs |
| Print | include `@page`, `break-inside`, `print-color-adjust` |

Minimal CSS hooks:

```css
:root {
  --parchment: #f5f4ed;
  --ivory: #faf9f5;
  --brand: #1B365D;
  --near-black: #141413;
  --stone: #6b6a64;
  --border: #e8e6dc;
}

body {
  background: var(--parchment);
  color: var(--near-black);
  font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", Georgia, serif;
  line-height: 1.55;
}

.editable-block:focus {
  outline: 1.5pt solid var(--brand);
  outline-offset: 4pt;
  background: var(--ivory);
}
```

The complete drop-in stylesheet is in [kami_academic.css](./kami_academic.css).
Use this contract file when adapting the theme to another base stylesheet;
use the `.css` file when embedding directly into an HTML output.

Sources: tw93/Kami README.md, references/design.md, CHEATSHEET.md.

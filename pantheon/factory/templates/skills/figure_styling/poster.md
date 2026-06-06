# Poster Recipe

| Field | Value |
|---|---|
| UI outputType | `poster` |
| Primary rendering mode | mixed |
| Best for | conference posters, workshop posters, teaching displays |
| Do not use for | single data plots, journal figures, slide decks |
| Read path | `SKILL.md` + this file |

---

## Visual goal

An academic conference poster readable from 1–2 meters. Combines code-rendered data panels with a flat schematic model. Clean layout, minimal text, high visual impact.

---

## Poster zones

Use a clear academic poster structure:

| Zone | Content |
|---|---|
| Title strip | title (≤18 words), authors, affiliation, logos |
| Column 1 | background + methods schematic |
| Column 2 | main results — code-rendered data panels |
| Column 3 | model schematic + interpretation + take-home message |

**Mixed rendering rule:**
- Column 2 data panels → code-first (matplotlib/seaborn)
- Column 1 and Column 3 schematics → AI-first (flat vector illustration)

---

## Layout metadata

| Property | Value |
|---|---|
| Dimensions | A0 (841×1189 mm) or 48×36 inches |
| Orientation | landscape (horizontal) preferred |
| Resolution | 150–200 dpi minimum |
| Columns | 3 (equal width) |
| Margins | ≥20mm outer, ≥15mm between columns |
| Background | white or very light grey (#f8f8f8) |

---

## Word limits (readable at 1–2 m distance)

| Element | Limit |
|---|---|
| Title | ≤18 words |
| Section heading | ≤5 words |
| Body block | ≤45–60 words |
| Figure caption | ≤25 words |
| Take-home message | ≤20 words |

---

## Typography

| Element | Font size |
|---|---|
| Title | ≥70pt |
| Section heading | ≥36pt |
| Body text | ≥24pt |
| Figure caption | ≥18pt |
| Take-home message | ≥28pt, bold |

Font: Arial, Helvetica, or similar clean sans-serif. Never use decorative fonts.

---

## Color and visual consistency

- Use 1–2 accent colors throughout; never >4 total colors.
- Data panels use colorblind-safe palette (seaborn `colorblind` or `tab10`).
- Schematics use the same accent colors as the data panels.
- White or very light background for all panels.
- Avoid gradients in main layout areas.

---

## Code panel defaults

Use `figure.md` style defaults for all code-rendered data panels:

```python
POSTER_PANEL_DEFAULTS = {
    "figsize": (5, 4),          # per panel; adjust for column width
    "dpi": 150,
    "font_family": "Arial",
    "font_size": 11,            # larger than journal — needs visibility
    "label_size": 12,
    "title_size": 13,
    "spine_top": False,
    "spine_right": False,
    "palette": "colorblind",
    "legend_frameon": False,
}
```

---

## AI schematic prompt pattern

For Column 1 and Column 3 schematic panels, use the flat vector scientific style:

```
[STYLE]
Flat vector scientific illustration. Clean white background. Restrained palette (2-3 colors + black). Editable-looking vector composition. Journal-grade clarity. No shadows or 3D effects.

[LAYOUT]
Describe the schematic composition and spatial zones.

[ENTITIES]
List concrete domain entities with their visual form (e.g., "oval = domain, circle = cell, rectangle = process step").

[CONNECTIONS]
Describe arrows and interactions. Solid arrow = flow/activation. Dashed = modulation/inhibition.

[LABELS]
Short labels only. No full sentences. Max 3–5 words per label.

[NEGATIVE]
No decorative 3D, no photorealistic glow, no fake charts or axes, no dense text blocks, no abstract boxes without domain meaning, no cartoon animals.
```

---

## Domain-specific example: 3D genomics poster

For a 3D genomics poster:
- Column 1: experimental workflow schematic (Hi-C library prep → sequencing → contact map) + brief methods text
- Column 2: contact map panel, TAD boundary analysis, insulation score comparison, or loop strength quantification
- Column 3: regulatory mechanism schematic (chromatin topology → enhancer–promoter interaction → transcriptional outcome) + take-home message

Entities for Column 3 schematic: chromatin loop, TAD boundary, enhancer (filled circle), promoter (rounded rectangle), CTCF/cohesin, gene body, transcription arrows.

*This is a domain example only — the recipe works for any scientific field.*

---

## Common mistakes

- Font size <18pt — unreadable at poster distance.
- Too much text — poster is not a paper.
- Mismatch between schematic style and data panel palette.
- Missing take-home message.
- Data panels not exported at ≥150 dpi.
- Mixing >4 colors across the poster.
- Using data panels from papers directly without reformatting for poster scale.

---

## Quick self-check

- [ ] Title ≤18 words
- [ ] Section headings ≤5 words
- [ ] All body text ≥24pt
- [ ] Data panels ≥150 dpi
- [ ] Colorblind-safe palette in data panels
- [ ] Schematic uses flat vector style (no 3D/glow)
- [ ] Take-home message present and ≤20 words
- [ ] ≤4 total colors across poster
- [ ] Column 1/3 schematics match color scheme of Column 2 data panels

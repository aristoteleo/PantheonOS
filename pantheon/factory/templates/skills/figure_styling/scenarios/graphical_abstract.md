---
id: graphical_abstract_scenario
name: "Graphical Abstract Scenario"
description: |
  Workflow for producing journal graphical abstracts — a single visual
  summary image that communicates the paper's key message at a glance.
  Follows Cell Press / Nature / Elsevier guidelines.
---

# Graphical Abstract Scenario

## When to Use

- Frontend `scenarioId`: `"graphical-abstract"` (or `outputType: "graphical-abstract"`)
- User says: "图形摘要", "graphical abstract", "visual abstract", "TOC graphic", "journal cover"
- Goal: Single striking image summarizing the paper for journal submission header / TOC
- Output: PNG (high-res) — no PDF/SVG required by most journals, but produce if asked

---

## Scenario Constraints

| Parameter | Value |
|-----------|-------|
| `target` | `journal` |
| `dpi_preview` | 300 |
| `dpi_final` | 600 |
| `export_formats` | `["png"]` (+ `"pdf"` if user says "投稿") |
| `font_family` | Arial / Helvetica |

**Size defaults by journal**:
- Cell Press (Cell, Cell Reports, etc.): `[6.69, 2.36]` inches (169 × 60 mm, landscape)
- Nature family: `[3.54, 2.36]` inches (90 × 60 mm, portrait/square-ish)
- Elsevier (Molecular Cell etc.): `[5.12, 2.76]` inches (130 × 70 mm)
- Default / unknown: `[5.5, 2.5]` inches landscape

**Infer journal from user input**: "Cell" → Cell Press dims; "Nature" → Nature dims; default if unclear.

---

## Style Card Defaults

```json
{
  "target": "journal",
  "aesthetic_guide": "nature_figure",
  "dpi_preview": 300,
  "dpi_final": 600,
  "figure_size_inches": { "single_column": [5.5, 2.5] },
  "font_family": "Arial",
  "font_size": { "label": 8, "annotation": 7, "panel_letter": 8 },
  "colors": {
    "categorical_palette": ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"]
  },
  "export_formats": ["png"]
}
```

`aesthetic_guide: "nature_figure"` applies because graphical abstracts target CNS-family journals. This enforces 7–8pt Arial, Paul Tol bright palette, white background, and no gridlines — all appropriate for journal header visuals.

**Override for ML-domain papers** (NeurIPS/ICML graphical abstracts): set `aesthetic_guide: "neurips_diagram"` in style_card to get the soft-pastel zone style instead.

---

## Graphical Abstract Design Principles

A graphical abstract is **not** a figure from the paper — it is a **standalone communication piece**:

1. **Single message**: One clear takeaway in ≤ 5 seconds of viewing
2. **Minimal text**: Labels only — no sentences, no legend prose
3. **Left → right narrative**: Input/problem on left, method/process in middle, result/impact on right
4. **Bold visual hierarchy**: Key result or molecule/cell is the largest, most saturated element
5. **White background**: Clean, minimal — no colored background zones unless used for grouping
6. **No axes or chart furniture**: Even data elements (bars, dots) should be simplified/stylized

**Structure template**:
```
[Context / Problem]  →  [Method / Mechanism]  →  [Key Result / Impact]
     (icon/schematic)        (process arrow)         (outcome image)
```

---

## Workflow

```
<graph_settings> parse → intent detection
  → journal size inference (from user message)
  → brief.json (illustration-only, single figure)
  → style_card.json (graphical abstract defaults)
  → illustrator pipeline (Plan → Style → Render → Critic)
  → critic enforces: single-panel, no caption text, narrative flow
  → quality check → delivery
```

### Step 1: Intent is almost always `illustration-only`

Graphical abstracts are conceptual illustrations. Only route to `data_plotter` if user explicitly says "include actual data" (rare — usually a stylized version of a result plot, not raw data).

### Step 2: Journal size detection

Scan user message for journal name keywords:
- "Cell", "Molecular Cell", "Cell Reports" → Cell Press: `[6.69, 2.36]`
- "Nature", "Nature Methods", "Nature Communications" → `[3.54, 2.36]`
- "Elsevier", "Journal of" → `[5.12, 2.76]`
- No match → default `[5.5, 2.5]`

Set in `style_card.figure_size_inches.single_column`.

### Step 3: Illustrator Phase 1 (Plan) guidance

Pass to `illustrator` in the instruction:
> "This is a graphical abstract. The design must communicate ONE key message visually. Use a strict left-to-right narrative: context/problem → method → outcome. Minimize text to labels only. The most important result should be the visually dominant element."

### Step 4: Critic guardrails (graphical abstract specific)

Add to critic instructions:
- Reject if more than one distinct narrative thread is present
- Reject if text labels form sentences (should be ≤ 3 words each)
- Reject if background is not white or very light
- Reject if there is no clear left → right flow

---

## Quality Checklist

- [ ] Single coherent message communicated in ≤ 5 seconds
- [ ] Left → right narrative flow
- [ ] No sentences — labels only (≤ 3 words each)
- [ ] White or near-white background
- [ ] Key result is the largest / most visually prominent element
- [ ] Dimensions match target journal specs
- [ ] No caption text embedded in image

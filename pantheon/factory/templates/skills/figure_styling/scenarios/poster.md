---
id: poster_scenario
name: "Poster Scenario"
description: |
  Workflow for producing academic conference posters (A0/A1 portrait or
  landscape), workshop teaching materials, and large-format displays.
---

# Poster Scenario

## When to Use

- Frontend `scenarioId`: `"poster"` (or `outputType: "poster"`)
- User says: "学术海报", "conference poster", "会议海报", "A0 poster", "workshop poster"
- Goal: Large-format poster for physical or digital display at conferences
- Output: PNG (high-res) + PDF (print-ready)

---

## Scenario Constraints

| Parameter | Value |
|-----------|-------|
| `target` | `slides` (large font, vivid color OK) |
| `dpi_preview` | 150 |
| `dpi_final` | 300 (sufficient for print at A0) |
| `export_formats` | `["png", "pdf"]` |
| `font_family` | Arial / Helvetica |
| `font_size.body` | 24 pt minimum |
| `font_size.heading` | 36–48 pt |
| `font_size.title` | 60–72 pt |

**Poster size defaults**:
- A0 portrait: `[33.1, 46.8]` inches → use as guidance for aspect ratio
- A0 landscape: `[46.8, 33.1]` inches
- **Delivered as composite PNG**: agent composes all panels into one image

**Aspect ratio rule**: Portrait posters → 0.7 : 1 (width : height). Landscape → 1.4 : 1.

---

## Style Card Defaults

```json
{
  "target": "slides",
  "aesthetic_guide": "neurips_diagram",
  "dpi_preview": 150,
  "dpi_final": 300,
  "figure_size_inches": { "single_column": [8.0, 6.0], "panel": [7.0, 5.0] },
  "font_family": "Arial",
  "font_size": { "axis_label": 18, "tick": 14, "legend": 14, "title": 22, "panel_letter": 24 },
  "export_formats": ["png", "pdf"]
}
```

Override `aesthetic_guide`:
- `nature-science` → keep `neurips_plot` but increase font sizes
- `minimalist` → `null` + notes: "White background, thin lines, muted palette, generous whitespace"
- `modern` → `null` + notes: "Bold accent color for section headers, flat icons, high contrast"

---

## Poster Structure (Standard Academic Layout)

Posters typically follow a 3–4 column grid:

```
┌─────────────────────────────────────────┐
│          TITLE  ·  Authors  ·  Logos    │ ← banner
├───────────┬───────────┬─────────────────┤
│ Background│  Method   │    Results      │
│  & Motiv. │  Overview │  (main figure)  │
├───────────┴───────────┼─────────────────┤
│     Experiments       │  Conclusion &   │
│     (sub-figures)     │  Future Work    │
├───────────────────────┴─────────────────┤
│  References  ·  QR Code  ·  Contact    │ ← footer
└─────────────────────────────────────────┘
```

**Leader's job**: decompose the poster into sections, assign each section to `data_plotter` (for result figures) or `illustrator` (for method overview diagrams), then compose using `data_plotter`'s multi-panel composition.

---

## Workflow

```
<graph_settings> parse → brief.json (poster intent)
  → style_card.json (poster defaults)
  → decompose into sections (banner / method / results / conclusion)
  → produce section figures in parallel
  → compose full poster PNG via data_plotter (Pillow)
  → export PDF via reportlab or inkscape
  → quality check (readability at 1m distance)
```

### Step 1: Poster decomposition

Based on user's input, infer which sections are needed. Write a section manifest in `brief.json`:

```json
{
  "intent": "composite-panel",
  "layout": "poster_3col",
  "figures": [
    { "id": "method_overview", "category": "agent_reasoning", ... },
    { "id": "main_result", "category": "statistical_plot", ... },
    { "id": "ablation", "category": "statistical_plot", ... }
  ]
}
```

### Step 2: Font size scaling

Poster fonts must be legible from 1 meter. Use a **dynamic scale factor** based on the target poster size rather than a hardcoded 2.5×:

```python
# Dynamic font scale — based on target poster physical size vs. journal baseline
# Journal baseline: single column 3.5" at 600 DPI
# A0 poster at 300 DPI: 33.1" wide → scale ≈ 33.1 / 3.5 × (300/600) ≈ 4.7×
# A1 poster at 300 DPI: 23.4" wide → scale ≈ 23.4 / 3.5 × (300/600) ≈ 3.3×
# Digital display (1920px, 96 DPI): 20" wide → scale ≈ 20 / 3.5 × (96/600) ≈ 0.9× → use 2.5× min

POSTER_WIDTH_INCHES = {
    "A0": 33.1, "A1": 23.4, "A2": 16.5,
    "custom": style.get("poster_width_inches", 33.1),
}.get(style.get("poster_size", "A0"), 33.1)

JOURNAL_COL_INCHES = 3.5
DPI_RATIO = style["dpi_final"] / 600
SCALE = max(2.5, (POSTER_WIDTH_INCHES / JOURNAL_COL_INCHES) * DPI_RATIO)

# Apply to all font sizes
for key in ["axes.labelsize", "xtick.labelsize", "ytick.labelsize", "legend.fontsize", "axes.titlesize"]:
    mpl.rcParams[key] = round(mpl.rcParams[key] * SCALE)

# Minimum sizes (readability floor)
mpl.rcParams["axes.labelsize"] = max(18, mpl.rcParams["axes.labelsize"])
mpl.rcParams["xtick.labelsize"] = max(14, mpl.rcParams["xtick.labelsize"])
mpl.rcParams["ytick.labelsize"] = max(14, mpl.rcParams["ytick.labelsize"])
```

Use **`vibrant`** palette from `figure_styling/styles/color_palettes.md` for poster figures — more vivid than `bright`, better at distance.

### Step 3: Color & contrast

Vivid color is acceptable for posters. Use `<graph_settings>.colorScheme` to anchor primary/secondary. Ensure:
- Title banner uses `colorScheme.colors[0]` as background or accent
- Section headers use a lighter tint (~30%) of primary color

### Step 4: Composition

`data_plotter` composes all section images into the full poster:
```python
# Use Pillow for raster composition
from PIL import Image
# Place each section PNG at correct grid position
# Export at 300 DPI → A0-ready print file
```

---

## Quality Checklist

- [ ] All text legible at 1 meter (≥ 24 pt equivalent)
- [ ] Title clearly visible from across the room
- [ ] Method diagram and main result figure are the visual focus
- [ ] QR code / contact info in footer (if user provides)
- [ ] DPI 300 minimum on final PNG
- [ ] PDF export suitable for print shop

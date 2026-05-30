---
id: presentation_scenario
name: "Presentation Scenario"
description: |
  Workflow for producing slide figures — 16:9 widescreen format,
  larger fonts, vivid palette. Targets oral presentations, group
  meetings, and conference talks.
---

# Presentation Scenario

## When to Use

- Frontend `scenarioId`: `"presentation"` (or `outputType: "presentation"`)
- User says: "幻灯片配图", "slides", "PPT图", "oral presentation", "演示", "组会图"
- Goal: Figures optimized for projected display — large text, high contrast, 16:9
- Output: PNG (screen-res + print-res)

---

## Scenario Constraints

| Parameter | Value |
|-----------|-------|
| `target` | `slides` |
| `dpi_preview` | 150 |
| `dpi_final` | 300 |
| `export_formats` | `["png"]` |
| `font_family` | Arial / Helvetica |
| `font_size.axis_label` | 14 pt |
| `font_size.tick` | 12 pt |
| `font_size.title` | 18 pt |
| `font_size.annotation` | 13 pt |

**Figure size defaults**:
- Standard slide figure: `[10.0, 5.6]` inches (16:9, fills most of slide)
- Half-slide (side-by-side): `[4.8, 5.0]` inches
- Quarter panel: `[4.8, 2.7]` inches

---

## Style Card Defaults

```json
{
  "target": "slides",
  "aesthetic_guide": null,
  "dpi_preview": 150,
  "dpi_final": 300,
  "figure_size_inches": { "single_column": [10.0, 5.6], "half": [4.8, 5.0] },
  "font_family": "Arial",
  "font_size": { "axis_label": 14, "tick": 12, "legend": 12, "title": 18, "panel_letter": 16 },
  "colors": {
    "categorical_palette": ["#EE7733", "#0077BB", "#33BBEE", "#EE3377", "#CC3311", "#009988", "#BBBBBB"]
  },
  "export_formats": ["png"]
}
```

Default `categorical_palette` is Paul Tol **`vibrant`** (from `figure_styling/styles/color_palettes.md`) — more vivid than `bright`, better legibility at projection distance. Override with `bright` for accessibility-critical audiences or `high-vis` for colorblind-critical contexts.

**`aesthetic_guide` based on `<graph_settings>.style`**:
- `nature-science` → `neurips_plot` (scientific precision)
- `modern` → `null` + notes: "Flat design, bold accent color, white background, clean spines-off style"
- `minimalist` → `null` + notes: "White background, open spines, no grid, only key data elements"
- `3d-render` → `null` + notes: "Isometric 3D perspective, clean shadows, vibrant colors"

---

## Slide Figure Design Principles

Slide figures have different rules than journal figures:

1. **Legibility at distance**: Viewer is 2–5 meters from screen. All text ≥ 14 pt.
2. **One figure = one message**: Do not cram multi-panel unless each panel is truly simple.
3. **High contrast**: Background white or very dark; data elements use saturated colors.
4. **Minimal axis furniture**: Remove spines (top + right at minimum); reduce ticks.
5. **Large markers**: Line chart markers ≥ 8 pt; bar chart bars wide with clear separation.
6. **Animation-friendly**: For `data-only` plots, produce a clean base PNG (no progressive reveal — that's the presenter's job in PowerPoint).

---

## Workflow

```
<graph_settings> parse → intent triage
  → brief.json (slide format, 16:9)
  → style_card.json (slide defaults)
  → figure production
  → font size verification (all labels ≥ 12 pt)
  → quality check → delivery
```

### Step 1: Figure size

Always use 16:9 proportions: `[10.0, 5.6]` unless user specifies "half slide" or specific dims.

Set in matplotlib:
```python
fig = plt.figure(figsize=(10.0, 5.6))
```

### Step 2: Font size scaling

Apply slide-specific rcParams override on top of style_card baseline:
```python
mpl.rcParams.update({
    "font.size": 13,
    "axes.labelsize": 14,
    "xtick.labelsize": 12,
    "ytick.labelsize": 12,
    "legend.fontsize": 12,
    "axes.titlesize": 18,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.2,
})
```

### Step 3: Color

Apply `<graph_settings>.colorScheme.colors[0]` as the dominant data color (first category, primary line, etc.). Use vivid / saturated version — acceptable for slides.

### Step 4: Critic extra check

Verify in Phase 4:
- All axis labels ≥ 12 pt (observer check via `observe_images`)
- No clutter: if more than 6 categories, flag for simplification
- 16:9 aspect ratio confirmed

---

## Quality Checklist

- [ ] 16:9 aspect ratio
- [ ] All text ≥ 12 pt (readable projected)
- [ ] High contrast — data visible against background
- [ ] Minimal axis clutter (open spines, light grid)
- [ ] Single clear message per figure
- [ ] PNG at 300 DPI (crisp on large display)

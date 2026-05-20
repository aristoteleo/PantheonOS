---
id: figure_scenario
name: "Figure Scenario"
description: |
  Workflow for producing publication-ready scientific figures (data plots,
  methodology diagrams, or composite panels) targeting journals and
  top-tier ML/bio venues (Nature, Cell, NeurIPS, ICML, etc.).
---

# Figure Scenario

## When to Use

- Frontend `scenarioId`: `"figure"` (or `outputType: "figure"`)
- User says: "论文图", "科学图表", "发表图", "publication figure", "Figure 1", "journal figure"
- Goal: Produce print-quality figure suitable for journal / conference paper submission
- Output: PNG (always) + PDF + SVG (for journal submissions)

---

## Scenario Constraints

| Parameter | Value |
|-----------|-------|
| `target` | `journal` |
| `dpi_preview` | 300 |
| `dpi_final` | 600 |
| `export_formats` | `["png", "pdf", "svg"]` |
| `font_family` | Arial / Helvetica (sans-serif) |
| `font_size.axis_label` | 9 pt |
| `font_size.tick` | 8 pt |
| `font_size.title` | 10 pt |

**Figure size defaults (inches)**:
- Single column: `[3.3, 2.5]` (Nature/IEEE standard)
- Double column: `[7.0, 5.0]`
- Square (heatmap, radar): `[3.3, 3.3]`

**Aspect ratio rule**: For methodology/framework diagrams, enforce 1.5 : 1 to 2.5 : 1 landscape. For statistical plots, use journal column width.

---

## Style Card Defaults

```json
{
  "target": "journal",
  "aesthetic_guide": "neurips_plot",
  "dpi_preview": 300,
  "dpi_final": 600,
  "figure_size_inches": { "single_column": [3.3, 2.5], "double_column": [7.0, 5.0] },
  "font_family": "Arial",
  "font_size": { "axis_label": 9, "tick": 8, "legend": 8, "title": 10, "panel_letter": 11 },
  "export_formats": ["png", "pdf", "svg"]
}
```

Override `aesthetic_guide`:
- For data plots → `neurips_plot` (default) or `nature_figure` / `ieee_figure`
- For methodology diagrams → `neurips_diagram`
- Guided by `style` field from `<graph_settings>`:
  - `nature-science` → use `nature_figure` style
  - `minimalist` → `null` (style_card notes: "Open spines, no grid, muted palette")
  - `modern` → `null` (style_card notes: "Flat design, bold accent, clean white background")

---

## Workflow

```
<graph_settings> parse → intent triage → reference detection
  → brief.json → style_card.json (this scenario's defaults)
  → figure production (data_plotter / illustrator)
  → critic loop (T ≤ 3) → vectorization (PDF + SVG)
  → observe_images quality check → delivery
```

### Step 1: Intent triage (from leader standard triage)

Classify based on user input and `<graph_settings>`:
- Has data file → `data-only` → `data_plotter`
- No data, conceptual description → `illustration-only` → `illustrator`
- Both → `composite-panel` → both agents in parallel

### Step 2: Style card initialization

Apply this scenario's defaults first. Then layer overrides:
1. `<graph_settings>.colorScheme.colors[0]` → `colors.primary`
2. `<graph_settings>.colorScheme.colors[1]` → `colors.secondary`
3. `<graph_settings>.style` → map to `aesthetic_guide` per table above
4. `<graph_settings>.audience == "expert"` → keep 8–10pt fonts; `== "graduate"` → bump 1pt

### Step 3: Figure production

`data_plotter` critic loop: T ≤ **3** rounds (journal quality requires more iteration).

`illustrator` critic loop: T ≤ **3** rounds.

### Step 4: Mandatory guardrails (journal-specific)

In addition to universal guardrails:
- All text in figures must be **editable** in the exported SVG/PDF (`pdf.fonttype: 42`, `svg.fonttype: "none"`)
- No embedded raster in SVG when vector alternatives exist
- Panel letters (a, b, c …) must be **bold, top-left corner** of each panel. Size is determined by `aesthetic_guide`: `nature_figure` → 8 pt; `neurips_*` → 11 pt; `ieee_figure` → 9 pt; default → `style_card.font_size.panel_letter`
- Color-blind safe palette required unless user explicitly overrides: prefer Paul Tol's bright/vibrant sets

### Step 5: Export

Always produce: PNG (600 DPI), PDF (vector), SVG (editable).

---

## Quality Checklist (leader verify before delivery)

- [ ] All fonts are sans-serif on axis labels
- [ ] DPI ≥ 600 on final PNG
- [ ] PDF and SVG contain vector text (not rasterized)
- [ ] No caption text embedded inside figure image
- [ ] Aspect ratio matches brief.json spec
- [ ] Color palette is colorblind-accessible
- [ ] Panel letters present and correctly formatted

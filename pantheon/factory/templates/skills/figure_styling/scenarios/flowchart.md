---
id: flowchart_scenario
name: "Flowchart Scenario"
description: |
  Workflow for producing methodology flowcharts, pipeline schematics,
  experimental protocols, and mechanism diagrams. Pure illustration —
  no data required. Routes exclusively to illustrator.
---

# Flowchart Scenario

## When to Use

- Frontend `scenarioId`: `"flowchart"` (or `outputType: "flowchart"`)
- User says: "流程图", "机制图", "实验流程", "pipeline", "架构图", "flowchart", "protocol diagram"
- Goal: Clear step-by-step process or mechanism diagram
- Output: PNG + SVG (for editable modification)

---

## Scenario Constraints

| Parameter | Value |
|-----------|-------|
| `target` | `journal` or `internal` (infer from context) |
| `dpi_preview` | 300 |
| `dpi_final` | 600 |
| `export_formats` | `["png", "svg"]` |
| `font_family` | Arial / Helvetica |

**Aspect ratio rule**: Flowcharts use the most flexible aspect ratios:
- Horizontal flow (left → right): 2.0 : 1 to 3.0 : 1
- Vertical flow (top → bottom): 0.7 : 1 to 1.0 : 1
- Circular / cyclic: 1.0 : 1 (square)

Infer from `<graph_settings>.layout`:
- `horizontal` → aspect ratio 2.2 : 1
- `vertical` → aspect ratio 0.8 : 1
- `circular` → aspect ratio 1.0 : 1

---

## Style Card Defaults

```json
{
  "target": "journal",
  "aesthetic_guide": "neurips_diagram",
  "dpi_preview": 300,
  "dpi_final": 600,
  "figure_size_inches": { "single_column": [7.0, 3.2] },
  "font_family": "Arial",
  "font_size": { "label": 9, "annotation": 8, "step_number": 10 },
  "export_formats": ["png", "svg"]
}
```

**`aesthetic_guide` based on `<graph_settings>.style`**:
- `nature-science` → `neurips_diagram` (clean, professional)
- `minimalist` → `null` + notes: "Flat arrows, no shadows, monochrome palette with one accent color"
- `modern` → `null` + notes: "Rounded steps, gradient fills, flat icons for each step"
- `realistic` → `null` + notes: "Use realistic icons / photos for inputs/outputs alongside step boxes"
- `3d-render` → `null` + notes: "Isometric 3D steps, depth shadows, vivid color scheme"

---

## Intent Routing

Flowchart scenario is **always `illustration-only`** — routes exclusively to `illustrator`.

No `data_plotter` involvement unless the user explicitly says "include a statistics summary panel" — in that case, treat as `composite-panel`.

---

## Flowchart Design Principles

1. **Linear clarity**: Each step flows unambiguously to the next. Use consistent arrow direction.
2. **Numbered steps** (optional but recommended for complex protocols): Step numbers in circles at top-left of each box.
3. **Color coding by phase**: Use background zone colors to group related steps (e.g., "Sample Prep" = pale blue zone, "Analysis" = pale green zone).
4. **Decision diamonds**: Use ⬥ diamond shape for conditional branches (Yes/No, Pass/Fail).
5. **Icon enhancement**: Add small illustrative icons inside or above step boxes to aid quick recognition.
6. **Consistent line weights**: Single line thickness for flow arrows; use arrowheads consistently.

---

## Workflow

```
<graph_settings> parse → layout detection (horizontal/vertical/circular)
  → aspect ratio assignment
  → brief.json (illustration-only)
  → style_card.json (flowchart defaults)
  → illustrator pipeline
  → Phase 1 (Plan): enumerate all steps, group by phase, define connections
  → Phase 2 (Style): apply zone colors, icon styles, arrow styles
  → Phase 3 (Render): generate_image
  → Phase 4 (Critic): verify step completeness, arrow correctness, label readability
  → SVG vectorization (inkscape)
  → delivery
```

### Step 1: Layout inference

From `<graph_settings>.layout`:
- `horizontal` → left-to-right sequential steps
- `vertical` → top-to-bottom, like experimental protocol
- `circular` → cyclic process (training loop, data pipeline cycle)
- `2-part` / `3-part` → split into parallel branches

Set aspect ratio accordingly in `brief.json.aspect_ratio`.

### Step 2: Illustrator Phase 1 — step enumeration

The Plan document must explicitly list every step:
```
Step 1: [label] — input: [what enters], output: [what exits]
Step 2: [label] — ...
Decision point: [condition] → Yes: Step X, No: Step Y
Phase grouping: Steps 1-3 = "Data Preprocessing", Steps 4-6 = "Model Training"
```

### Step 3: SVG export

Always request SVG (editable for downstream modification in Inkscape/Illustrator):
```bash
inkscape {input}.png --export-type=svg --export-filename={output}.svg
```

### Step 4: Critic — flowchart-specific checks

- All steps present and correctly connected
- No ambiguous arrows (every arrow has a clear destination)
- Decision points have both Yes and No branches labeled
- Labels are concise (≤ 5 words per step box)
- Phase groupings visually distinct

---

## Quality Checklist

- [ ] All steps from user description are present
- [ ] Arrows have clear direction and destination
- [ ] Decision branches labeled (Yes/No)
- [ ] Phase groupings visually distinct
- [ ] Labels ≤ 5 words per step
- [ ] SVG export confirmed editable (vector text)
- [ ] No caption text inside image

---
id: figure_styling_skills_index
name: Figure Styling Skills Index
description: |
  Complete skill library for the Graph Maker Team. Covers scenario workflows,
  aesthetic style guides, input optimization prompts, quality evaluation
  prompts, and triage rules for scientific figure production.
---

# Figure Styling Skills

Resources for the Graph Maker Team leader, illustrator, and data_plotter.

## Execution Order (leader + sub-agents)

```
leader:
  Phase 0 — Input Optimization    → input/context_enricher, input/caption_sharpener
  Phase 1 — Intent Triage         → triage/figure_type_classifier, triage/granularity_rule
  Phase 2 — Scenario Detection    → scenarios/<id>.md
  Phase 3 — Style Card Init       → styles/<aesthetic_guide>.md

illustrator (per figure):
  Phase 1 — Plan                  → quality/diagram_planner.md
             (bio/chem only)      → quality/scientific_schematic.md (IR extraction)
  Phase 2 — Style                 → quality/diagram_stylist.md
  Phase 3 — Render                → generate_image
  Phase 4 — Critic                → quality/diagram_critic.md (+ quality thresholds)

data_plotter (per figure):
  Pre-code  — Plan                → quality/plot_planner.md (optional)
  Pre-code  — Style               → quality/plot_stylist.md (optional)
  Code gen  — Visualizer          → quality/plot_visualizer.md
  Review    — Critic              → quality/plot_critic.md (+ quality thresholds)
  Refine    — Visual feedback     → quality/visual_refine.md (fallback)
```

---

## Scenarios

Scenario files define the full workflow, style card defaults, guardrails, and
quality checklist for each output type. **Leader reads the scenario file before
initializing style_card.json.**

| Scenario ID | File | Frontend `outputType` | Use case |
|---|---|---|---|
| `figure` | [scenarios/figure.md](./scenarios/figure.md) | `"figure"` | Publication-ready journal / conference figures |
| `poster` | [scenarios/poster.md](./scenarios/poster.md) | `"poster"` | Conference posters (A0/A1), workshop materials |
| `graphical-abstract` | [scenarios/graphical_abstract.md](./scenarios/graphical_abstract.md) | `"graphical-abstract"` | Journal graphical abstracts, visual TOC |
| `presentation` | [scenarios/presentation.md](./scenarios/presentation.md) | `"presentation"` | Slide figures (16:9), oral presentation visuals |
| `flowchart` | [scenarios/flowchart.md](./scenarios/flowchart.md) | `"flowchart"` | Methodology flowcharts, pipeline diagrams, protocol schematics |

---

## Style Files

Aesthetic rules (palettes, typography, matplotlib rcParams) loaded by sub-agents.
Set `aesthetic_guide: "<style_id>"` in `style_card.json` to activate.

**Venue styles** — set `aesthetic_guide` to one of these:

| Style ID | File | Target venue | Key spec |
|---|---|---|---|
| `neurips_diagram` | [styles/neurips_diagram.md](./styles/neurips_diagram.md) | NeurIPS / ICML / ICLR / CVPR | Soft pastel methodology diagrams |
| `neurips_plot` | [styles/neurips_plot.md](./styles/neurips_plot.md) | NeurIPS / ICML / ICLR / CVPR | Sans-serif, open spines, dashed grid |
| `nature_figure` | [styles/nature_figure.md](./styles/nature_figure.md) | Nature / Cell / Science | 7 pt, inward ticks all 4 sides, no grid |
| `ieee_figure` | [styles/ieee_figure.md](./styles/ieee_figure.md) | IEEE journals / conferences | CM serif, k/r/b/g + linestyle, B&W compatible |

**Color palettes** — set `style_card.colors.categorical_palette` to the chosen array:

| Palette | File | Paul Tol | When to use |
|---|---|---|---|
| `bright` (default) | [styles/color_palettes.md](./styles/color_palettes.md) | ✅ | General scientific, CNS submissions |
| `vibrant` | same | ✅ | Posters, slides, graphical abstracts |
| `muted` | same | ✅ | Dense multi-category (7–9 groups) |
| `high-vis` | same | ✅ | Accessibility-critical, B&W printing |
| `retro` | same | Partial | Distinctive / preprint aesthetic |

**Visual quality reference** — always consulted in the final critic round:

| File | Purpose |
|---|---|
| [styles/visual_quality_checklist.md](./styles/visual_quality_checklist.md) | 6-tier Nature/Cell submission quality checklist (data integrity, data-ink ratio, typography, color, layout, reproducibility) |

---

## Input Optimization (Phase 0)

Run before triage when user input is vague. Source: PaperBanana (Apache-2.0).

| Module | File | Purpose |
|---|---|---|
| `context_enricher` | [input/context_enricher.md](./input/context_enricher.md) | Structure raw methodology text into components / flows / groupings |
| `caption_sharpener` | [input/caption_sharpener.md](./input/caption_sharpener.md) | Sharpen vague caption into precise visual specification |

---

## Triage Rules (Phase 1)

Classify figure type and decide if request should be split. Source: Codex-drawio-skill (MIT).

| Rule | File | Purpose |
|---|---|---|
| `figure_type_classifier` | [triage/figure_type_classifier.md](./triage/figure_type_classifier.md) | Classify into Architecture / Roadmap / Workflow / Plot / Schematic |
| `granularity_rule` | [triage/granularity_rule.md](./triage/granularity_rule.md) | Detect when request mixes multiple concerns → split into multiple figures |

---

## Quality Evaluation (sub-agent phases)

Critic prompts, planning prompts, styling prompts, and quality thresholds.
Source: PaperBanana (Apache-2.0), MatPlotAgent (MIT), imageGenV0 (MIT).

**Planning (Phase 1)**

| Prompt | File | Used by | Source |
|---|---|---|---|
| `diagram_planner` | [quality/diagram_planner.md](./quality/diagram_planner.md) | `illustrator` Phase 1 | PaperBanana |
| `scientific_schematic` | [quality/scientific_schematic.md](./quality/scientific_schematic.md) | `illustrator` Phase 1 (bio/chem) | imageGenV0 |
| `plot_planner` | [quality/plot_planner.md](./quality/plot_planner.md) | `data_plotter` pre-code | PaperBanana |

**Styling (Phase 2)**

| Prompt | File | Used by | Source |
|---|---|---|---|
| `diagram_stylist` | [quality/diagram_stylist.md](./quality/diagram_stylist.md) | `illustrator` Phase 2 | PaperBanana |
| `plot_stylist` | [quality/plot_stylist.md](./quality/plot_stylist.md) | `data_plotter` pre-code (optional) | PaperBanana |

**Code generation + Critic (Phase 3–4)**

| Prompt | File | Used by | Source |
|---|---|---|---|
| `plot_visualizer` | [quality/plot_visualizer.md](./quality/plot_visualizer.md) | `data_plotter` code gen | PaperBanana |
| `diagram_critic` | [quality/diagram_critic.md](./quality/diagram_critic.md) | `illustrator` Phase 4 | PaperBanana |
| `plot_critic` | [quality/plot_critic.md](./quality/plot_critic.md) | `data_plotter` review | PaperBanana |
| `visual_refine` | [quality/visual_refine.md](./quality/visual_refine.md) | `data_plotter` vision feedback | MatPlotAgent |

**Post-generation (Step 9)**

| Prompt | File | Used by | Source |
|---|---|---|---|
| `figure_caption` | [quality/figure_caption.md](./quality/figure_caption.md) | `leader` Step 9 — caption generation | PaperBanana |

**Delivery validation (Step 8)**

| Check | File | Used by | Purpose |
|---|---|---|---|
| `figure_format_lint` | [quality/figure_format_lint.md](./quality/figure_format_lint.md) | `leader` Step 8 — pre-manifest verification | File naming, DPI, format integrity, caption completeness, numbering |

**Quality thresholds**

| Scenario | Threshold | Max iterations |
|---|---|---|
| `figure` / `graphical-abstract` | 8.5 / 10 | 3 |
| `flowchart` | 8.0 / 10 | 2 |
| `poster` | 7.0 / 10 | 2 |
| `presentation` | 6.5 / 10 | 1 |

---

## Priority Chain

```
user explicit request
  > <graph_settings> values
    > scenario file defaults
      > style file (aesthetic_guide)
        > sub-agent internal defaults
```

## Adding New Content

- **New scenario**: `scenarios/<id>.md` with frontmatter, workflow, style_card defaults, quality checklist → add row to Scenarios table
- **New style**: `styles/<id>.md` following section structure of existing files → add row to Style Files table
- **New input prompt**: `input/<id>.md` → add row to Input Optimization table
- **New triage rule**: `triage/<id>.md` → add row to Triage Rules table
- **New quality prompt**: `quality/<id>.md` → add row to Quality Evaluation table

---
id: figure_quality_index
name: Figure Quality Evaluation Index
description: |
  Quality evaluation prompts for the Graph Maker Team. Covers planning,
  styling, critic, and visual refinement prompts used across all phases.
  Adapted from llmsresearch/paperbanana (Apache-2.0), thunlp/MatPlotAgent
  (MIT), and rileydog53/imageGenV0 (MIT).
---

# Figure Quality Evaluation

## Quality Thresholds (by scenario)

| Scenario | Threshold | Max iterations T |
|---|---|---|
| `figure` / `graphical-abstract` | 8.5 / 10 | 3 |
| `flowchart` | 8.0 / 10 | 2 |
| `poster` | 7.0 / 10 | 2 |
| `presentation` | 6.5 / 10 | 1 |
| default (no scenario) | 8.0 / 10 | 2 |

**Early stop**: when `quality_score >= threshold` OR `revised_description == null` / `revised_code_hints == "No changes needed."`, the loop terminates regardless of remaining iterations.

## Four-Dimensional Evaluation Standard

| Dimension | Weight | What it checks |
|---|---|---|
| **Faithfulness** | 30% | All components present; no hallucinations; matches S and C |
| **Conciseness** | 20% | Labels ≤5 words; no clutter; no redundant text legend |
| **Readability** | 30% | Labels/flow clear; font size meets scenario minimum; no overlap |
| **Aesthetics** | 20% | Matches style_card + aesthetic_guide; publication finish |

`quality_score = 0.3×F + 0.2×C + 0.3×R + 0.2×A`

## Planning Prompts (Phase 1)

| Prompt | File | Used by | Source |
|---|---|---|---|
| `diagram_planner` | [diagram_planner.md](./diagram_planner.md) | `illustrator` Phase 1 | PaperBanana |
| `plot_planner` | [plot_planner.md](./plot_planner.md) | `data_plotter` pre-code | PaperBanana |

## Styling Prompts (Phase 2)

| Prompt | File | Used by | Source |
|---|---|---|---|
| `diagram_stylist` | [diagram_stylist.md](./diagram_stylist.md) | `illustrator` Phase 2 | PaperBanana |
| `plot_stylist` | [plot_stylist.md](./plot_stylist.md) | `data_plotter` pre-code (optional) | PaperBanana |

## Critic Prompts (Phase 4 / review loop)

| Prompt | File | Used by | Source |
|---|---|---|---|
| `diagram_critic` | [diagram_critic.md](./diagram_critic.md) | `illustrator` Phase 4 | PaperBanana |
| `plot_critic` | [plot_critic.md](./plot_critic.md) | `data_plotter` review loop | PaperBanana |
| `plot_visualizer` | [plot_visualizer.md](./plot_visualizer.md) | `data_plotter` code generation | PaperBanana |
| `visual_refine` | [visual_refine.md](./visual_refine.md) | `data_plotter` vision-based refinement | MatPlotAgent |

## Specialist Skills

| Skill | File | Used by | Source |
|---|---|---|---|
| `scientific_schematic` | [scientific_schematic.md](./scientific_schematic.md) | `illustrator` (bio/chem archetypes) | imageGenV0 |

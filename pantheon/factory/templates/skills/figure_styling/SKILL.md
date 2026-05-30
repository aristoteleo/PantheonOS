---
id: figure_styling_skills_index
name: Figure Styling Skills Index
description: |
  Aesthetic guidelines and quality control prompts for scientific figure production.
  Provides journal-specific styles (NeurIPS, Nature, IEEE), colorblind-safe palettes,
  and critic prompts for the Graph Maker Team's illustrator and data_plotter agents.
---

# Figure Styling Skills

Resources for the Graph Maker Team's `illustrator` (diagram) and `data_plotter` (plot) agents. The leader writes `aesthetic_guide: <style_id>` into `style_card.json`; the producing agent then loads the matching style file.

## Available Styles

| Style ID | File | Target | Figure class |
|---|---|---|---|
| `neurips_diagram` | [styles/neurips_diagram.md](./styles/neurips_diagram.md) | NeurIPS / ICML / ICLR / CVPR | Methodology / framework diagrams |
| `neurips_plot` | [styles/neurips_plot.md](./styles/neurips_plot.md) | NeurIPS / ICML / ICLR / CVPR | Statistical plots |
| `nature_figure` | [styles/nature_figure.md](./styles/nature_figure.md) | Nature / Cell / Science | All figure types |
| `ieee_figure` | [styles/ieee_figure.md](./styles/ieee_figure.md) | IEEE journals / conferences | Statistical plots |

## Color Palettes

[color_palettes.md](./styles/color_palettes.md) - Paul Tol colorblind-safe palettes (bright/vibrant/muted/light/high-vis/dark)

## Quality Control

| Prompt | File | Used by | Purpose |
|---|---|---|---|
| `diagram_critic` | [quality/diagram_critic.md](./quality/diagram_critic.md) | illustrator Phase 4 | Multi-round critique with quality_score |
| `plot_critic` | [quality/plot_critic.md](./quality/plot_critic.md) | data_plotter review | Statistical plot critique |
| `figure_caption` | [quality/figure_caption.md](./quality/figure_caption.md) | final delivery | Auto-generate publication captions |
| `visual_quality_checklist` | [styles/visual_quality_checklist.md](./styles/visual_quality_checklist.md) | All agents | 6-tier quality standards (Rougier/Tufte) |

Additional quality prompts (optional, loaded on demand by producing agents):

| Prompt | File | Purpose |
|---|---|---|
| `diagram_planner` | [quality/diagram_planner.md](./quality/diagram_planner.md) | Phase 1 semantic planning for diagrams |
| `diagram_stylist` | [quality/diagram_stylist.md](./quality/diagram_stylist.md) | Phase 2 aesthetic styling for diagrams |
| `plot_planner` | [quality/plot_planner.md](./quality/plot_planner.md) | Plot type selection and layout planning |
| `plot_stylist` | [quality/plot_stylist.md](./quality/plot_stylist.md) | Plot aesthetic refinement |
| `plot_visualizer` | [quality/plot_visualizer.md](./quality/plot_visualizer.md) | Render-time visualization hints |
| `visual_refine` | [quality/visual_refine.md](./quality/visual_refine.md) | Post-critic visual refinement loop |
| `scientific_schematic` | [quality/scientific_schematic.md](./quality/scientific_schematic.md) | Domain-specific scientific schematic rules |
| `figure_format_lint` | [quality/figure_format_lint.md](./quality/figure_format_lint.md) | Deliverable format compliance checks |
| `quality/index` | [quality/SKILL.md](./quality/SKILL.md) | Quality sub-index |

## Optional Input Skills

Pre-processing helpers for ambiguous or reference-heavy user input. **Not loaded by default** — only when leader detects the trigger condition.

| Prompt | File | Purpose |
|---|---|---|
| `context_enricher` | [input/context_enricher.md](./input/context_enricher.md) | Structure vague prose into components, flows, groupings |
| `caption_sharpener` | [input/caption_sharpener.md](./input/caption_sharpener.md) | Sharpen generic captions into precise 1-paragraph specs |
| `reference_retriever` | [input/reference_retriever.md](./input/reference_retriever.md) | Top-K selection from normalized reference pool |
| `input/index` | [input/SKILL.md](./input/SKILL.md) | Input sub-index |

## Optional Triage Skills

Figure type classification helpers. **Not loaded by default** — only when figure type is ambiguous.

| Prompt | File | Purpose |
|---|---|---|
| `figure_type_classifier` | [triage/figure_type_classifier.md](./triage/figure_type_classifier.md) | Classify request into 1 of 6 figure types |
| `granularity_rule` | [triage/granularity_rule.md](./triage/granularity_rule.md) | Split mixed requests into separate figure records |
| `triage/index` | [triage/SKILL.md](./triage/SKILL.md) | Triage sub-index |

## Optional Scenario Skills

Scenario-specific workflows and guardrails. **Loaded only when the scenario matches** (via keyword or explicit user request).

| Scenario | File | When to load |
|---|---|---|
| `figure` | [scenarios/figure.md](./scenarios/figure.md) | Journal submission figure |
| `flowchart` | [scenarios/flowchart.md](./scenarios/flowchart.md) | Pipeline / protocol / mechanism diagram |
| `graphical-abstract` | [scenarios/graphical_abstract.md](./scenarios/graphical_abstract.md) | Journal graphical abstract (Cell Press / Nature) |
| `poster` | [scenarios/poster.md](./scenarios/poster.md) | Conference poster (A0/A1) |
| `presentation` | [scenarios/presentation.md](./scenarios/presentation.md) | Slide figures (16:9) |

## Usage

1. Leader sets `aesthetic_guide: "<style_id>"` in `{workdir}/inputs/style_card.json`
2. Sub-agent reads the corresponding style file from this skill
3. Priority chain: **user references > style_card.json > figure_styling/<style_id> > agent defaults**

If `aesthetic_guide` is `custom` or `null`, agents rely purely on `style_card.json` and internal defaults.

## When to Load

Agents read files from this skill **on demand** — never preload all files. Use this decision table:

| Trigger | Read | Skip if |
|---------|------|---------|
| `aesthetic_guide = nature_figure` | `styles/nature_figure.md` | — |
| `aesthetic_guide = ieee_figure` | `styles/ieee_figure.md` | — |
| `aesthetic_guide = neurips_plot` | `styles/neurips_plot.md` | — |
| `aesthetic_guide = neurips_diagram` | `styles/neurips_diagram.md` | — |
| colorblind-safe palette required | `styles/color_palettes.md` | — |
| statistical plot + publication depth | `quality/plot_critic.md` | quick / draft / sketch tasks |
| methodology diagram + publication depth | `quality/diagram_critic.md` | quick / draft / sketch tasks |
| caption required (final delivery) | `quality/figure_caption.md` | quick / draft / sketch tasks |
| any figure type + publication depth | `styles/visual_quality_checklist.md` | quick / draft / sketch tasks |
| user input is vague prose (no components listed) | `input/context_enricher.md` | structured input already provided |
| caption is generic or missing | `input/caption_sharpener.md` | caption already names specific components |
| multiple visual references provided (K > 5) | `input/reference_retriever.md` | ≤5 references |
| figure type is ambiguous | `triage/figure_type_classifier.md` | clear data-only or illustration-only intent |
| request mixes multiple figure types | `triage/granularity_rule.md` | single clear figure type |
| scenario detected (keyword or explicit) | `scenarios/<scenario>.md` | no scenario match |
| quick / draft / sketch / "show me" depth | **skip all quality/ input/ triage/ scenarios/** | use `style_card.json` + one style file only |

**Rule of thumb**: quick tasks read ≤2 files (style_card style file + optionally color_palettes). Publication tasks additionally read the relevant critic + visual_quality_checklist. Input, triage, and scenario helpers are optional — only load when the trigger condition is met and the task is not quick/draft.

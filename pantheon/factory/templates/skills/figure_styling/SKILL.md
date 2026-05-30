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
| quick / draft / sketch / "show me" depth | **skip all quality/** prompts | use `style_card.json` + one style file only |

**Rule of thumb**: quick tasks read ≤2 files (style_card style file + optionally color_palettes). Publication tasks additionally read the relevant critic + visual_quality_checklist.

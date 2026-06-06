---
id: figure_styling_skills_index
name: Figure Styling Skills Index
description: |
  Aesthetic guidelines and output-type recipes for scientific figure production.
  Supports lightweight default-agent use through SKILL.md + one outputType recipe,
  with optional venue-specific style guides when requested. Also serves as the
  Graph Maker Team's style index when the full harness is in use.
---

# Figure Styling

General scientific figure styling skill for default agents and graph-generation tasks.

This skill has two layers:

1. **OutputType recipes** — self-contained recipes for default-agent use (`SKILL.md` + one recipe file).
2. **Optional style guides** — venue- or aesthetic-specific refinement, loaded only when requested.

**Lightweight read rule**: For normal use, read only this `SKILL.md` and one outputType recipe. Do not load `styles/`, `quality/`, `input/`, or `triage/` unless explicitly needed.

---

## Route by outputType

| outputType | Read | Rendering mode | Use for |
|---|---|---|---|
| `figure` | `figure.md` | code-first | paper figures, data plots, multi-panel figures |
| `poster` | `poster.md` | mixed | academic posters |
| `graphical-abstract` | `graphical-abstract.md` | AI-first | graphical abstracts, TOC figures |
| `presentation` | `presentation.md` | mixed | slide visuals |
| `flowchart` | `flowchart.md` | AI-first | workflow diagrams, process diagrams |

---

## Optional style guides

Read these only when the user explicitly requests a venue/style or a `style_id` is provided.
If a listed file is not present, continue with the outputType recipe and report the missing file.

| style_id | File | Use for |
|---|---|---|
| `neurips_diagram` | `styles/neurips_diagram.md` | ML methodology diagrams (NeurIPS / ICML / ICLR / CVPR) |
| `neurips_plot` | `styles/neurips_plot.md` | ML/statistical plots (NeurIPS / ICML / ICLR / CVPR) |
| `nature_figure` | `styles/nature_figure.md` | Nature / Cell / Science-style scientific figures |
| `ieee_figure` | `styles/ieee_figure.md` | IEEE journal / conference figures |
| `color_palettes` | `styles/color_palettes.md` | Colorblind-safe palette reference (Paul Tol) |

---

## Scientific visual language

Use concrete visual schemas instead of generic scientific prompts.

**Prefer:**
- Flat vector scientific illustration
- Clean white or very light background
- Short accurate labels
- Explicit entities and connections
- Editable-looking vector composition
- Journal-grade readability
- Clear module hierarchy
- Restrained scientific palette

**Avoid:**
- Generic AI art
- Decorative 3D or photorealistic glow
- Fake charts, fake axes, fake data in AI diagrams
- Dense tiny text or unreadable legends
- Abstract boxes without scientific meaning
- Childish cartoon style
- Heavy shadows or decorative gradients

---

## Domain adaptation rule

Do not assume a fixed scientific domain.

When the user provides a domain, instantiate the visual schema with that domain's concrete entities, structures, labels, and relationships.

Examples (not defaults):
- **3D genomics**: TAD, chromatin loop, boundary insulation, enhancer, promoter, CTCF/cohesin, Hi-C contact map, genome-browser tracks
- **Cell biology**: nucleus, mitochondria, ER, Golgi, membrane receptors, signaling pathway
- **Immunology**: T cell, antigen-presenting cell, receptor, cytokine, immune synapse
- **Neuroscience**: neuron, synapse, axon, dendrite, action potential
- **ML/systems**: encoder, decoder, attention, pipeline stage, data flow

These are domain instantiation examples, not default assumptions.

---

## Legacy compatibility

Existing Graph Maker Team workflows may continue to use `styles/` as venue-specific style guides via `aesthetic_guide` in `style_card.json`.

Default-agent use does not require:
- Graph Maker Team
- `leader`, `data_plotter`, or `illustrator` agents
- `canvas` or `workdir`
- `style_card.json`

The outputType recipes are self-contained.

## Custom styles

Additional `.md` style files may be added to `styles/` following the existing file structure. Set `aesthetic_guide: "<style_id>"` in `style_card.json` to activate for Graph Maker Team use.

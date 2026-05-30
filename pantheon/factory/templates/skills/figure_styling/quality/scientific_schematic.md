---
id: scientific_schematic
name: Scientific Schematic Generator
description: |
  Skill for generating publication-style biological/chemical schematics:
  pathway diagrams, reaction schemes, experimental workflows, cellular
  schematics, and mechanism cartoons. Vector-first (SVG/PNG/PDF).
  NOT for data plots or photorealistic images.
source: https://github.com/rileydog53/imageGenV0
license: MIT
---

# Scientific Schematic Generator

> **Source**: `SKILL.md` in
> [rileydog53/imageGenV0](https://github.com/rileydog53/imageGenV0) (MIT).

## Purpose

Generates publication-style schematic figures for biology/chemistry/medicine
scenarios. Routes to `illustrator` with a structured intermediate representation
(IR) that enforces semantic correctness before image generation.

## When to Use (Trigger conditions)

- Signalling / metabolic pathway diagram
- Chemical reaction scheme
- Experimental workflow / protocol
- Cellular schematic (organelles, membranes, compartments)
- Mechanism cartoon (drug inhibition, protein interaction)
- Multi-panel graphical abstract for biology papers

## When NOT to Use

- Photorealistic images → use image generation directly
- Real data plots (bar, line, scatter) → use `data_plotter`
- 3D molecular structures (PDB rendering) → specialist tool
- General ML/AI architecture diagrams → use `diagram_planner`

## 5 Archetypes

| Archetype | Description | Key requirements |
|---|---|---|
| `pathway` | Signalling/metabolic cascade with entities, compartments, relations | Inhibition T-bars; activation arrows; compartment boundaries |
| `reaction_scheme` | Chemical reactions with reactants, products, conditions | SMILES validation; arrow conventions; reagent labels |
| `workflow` | Experimental protocol steps | Step boxes; decision diamonds; fallback logic |
| `cellular_schematic` | Cell with organelles and molecular actors | Membrane boundaries; compartment labels; scale consistency |
| `mechanism_cartoon` | Drug/protein mechanism of action | Before/after states; interaction highlights |

## 3 Style Presets

| Preset | Description | Use case |
|---|---|---|
| `cell_press` | Rounded nodes, warm pastels, friendly | Cell, Nature Cell Biology, iScience |
| `nature` | Bold lines, colorblind-safe palette | Nature, Nature Methods, Nature Communications |
| `acs` | Monochrome, formal, minimal decoration | JACS, ACS Nano, chemical journals |

## Structured IR (Intermediate Representation)

Before generating, extract a validated IR from the user's description:

```json
{
  "archetype": "pathway",
  "style_preset": "cell_press",
  "title": "JAK-STAT Signaling in Activated T Cells",
  "entities": [
    {
      "id": "e1",
      "label": "IL-6",
      "type": "ligand",
      "compartment": "extracellular",
      "shape": "hexagon"
    },
    {
      "id": "e2",
      "label": "JAK1",
      "type": "kinase",
      "compartment": "membrane",
      "shape": "rounded_rect"
    }
  ],
  "compartments": [
    {"id": "c1", "label": "Extracellular", "color": "pale_blue"},
    {"id": "c2", "label": "Cytoplasm", "color": "pale_green"}
  ],
  "relations": [
    {
      "from": "e1",
      "to": "e2",
      "type": "activation",
      "arrow": "filled_arrow",
      "label": "binds"
    },
    {
      "from": "e3",
      "to": "e4",
      "type": "inhibition",
      "arrow": "T_bar",
      "label": "blocks"
    }
  ],
  "panels": [],
  "annotations": []
}
```

**Relation types → arrow conventions**:
- `activation` → filled arrowhead →
- `inhibition` → T-bar ⊣
- `binding` → double-headed arrow ↔
- `translocation` → dashed arrow - ->
- `phosphorylation` → circled P label on arrow

## Mandatory Workflow for `illustrator` (when archetype detected)

```
1. Classify → identify archetype from user description
2. Extract IR → build the JSON above from user's text
3. Validate IR → check: all relation endpoints exist; compartments assigned;
   no missing labels; arrow types are valid
4. Pass IR to Phase 1 (Plan) → use IR as the structured source context
   instead of raw text; guarantees semantic correctness
5. Phase 2 (Style) → apply style_preset + neurips_diagram aesthetic guide
6. Phase 3 (Render) → generate_image with biological accuracy emphasis
7. Phase 4 (Critic) → apply diagram_critic.md + verify:
   - Inhibition T-bars are T-shaped, NOT arrows
   - Activation arrows have filled arrowheads
   - Compartment boundaries are visible and labeled
   - All entities from IR are present in the image
```

## Quality Gate

Before accepting as final, `illustrator` must verify:
- [ ] All entities from IR are visible and labeled
- [ ] All inhibition relations use T-bar notation
- [ ] Compartment boundaries clearly delineated
- [ ] No text labels inside dark-colored shapes (use white text or label outside)
- [ ] Style preset palette applied consistently
- [ ] No caption text embedded in image

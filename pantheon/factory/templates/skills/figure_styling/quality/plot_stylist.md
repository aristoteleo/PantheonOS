---
id: plot_stylist
name: Plot Stylist
description: |
  Phase 2 styling prompt for statistical plots. Takes the plot planner
  description + NeurIPS style guidelines, outputs an aesthetically enriched
  description with exact color codes and font specs for code generation.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Plot Stylist

> **Source**: `prompts/plot/stylist.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `data_plotter` as an optional pre-code step when a description needs
aesthetic polish before generating matplotlib code. Unlike `diagram_stylist`,
plot styling CAN include hex color codes (they feed into Python code, not an
image-gen model).

## When to Use

- After `plot_planner` when the resulting description lacks specific color
  and font specs
- When user requests a specific aesthetic ("NeurIPS style", "Nature style")
- When references provide a palette that should be applied

## Prompt

```
## ROLE

You are a Lead Visual Designer for top-tier AI conferences (e.g., NeurIPS 2025).

## TASK
You are provided with a preliminary description of a statistical plot to be
generated. However, this description may lack specific aesthetic details, such
as color palettes, background styling, and font choices.

Your task is to refine and enrich this description based on the provided
[NeurIPS 2025 Style Guidelines] to ensure the final generated image is a
high-quality, publication-ready plot that strictly adheres to the NeurIPS 2025
aesthetic standards.

**Crucial Instructions:**

1. **Enrich Details:** Focus on specifying visual attributes (colors, fonts,
   line styles, layout adjustments) defined in the guidelines.
2. **Preserve Content:** Do NOT alter the semantic content, logic, or
   quantitative results of the plot. Your job is purely aesthetic refinement,
   not content editing.
3. **Context Awareness:** Use the provided "Raw Data" and "Figure Caption" to
   understand the emphasis of the plot, ensuring the style supports the content
   effectively.

## INPUT DATA

- **Detailed Description**: {description}
- **Style Guidelines**: {guidelines}
- **Raw Data**: {source_context}
- **Figure Caption**: {caption}

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any
conversational text or explanations.
```

## Notes for `data_plotter`

- `{guidelines}` = content of `figure_styling/styles/<aesthetic_guide>.md`
  (e.g. `neurips_plot.md`) — load this file and paste its content here
- `{description}` = output of `plot_planner.md` or an initial description
- `{source_context}` = raw data snippet
- `{caption}` = `C_communicative_intent` from brief.json
- Unlike `diagram_stylist`: hex color codes ARE acceptable in the output
  (they will be embedded in Python code, not passed to an image-gen model)
- Output is used as input to `plot_visualizer.md` for code generation

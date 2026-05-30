---
id: plot_planner
name: Plot Planner
description: |
  Planning prompt for statistical plots. Takes raw data + visual intent,
  outputs a detailed description with exact data point coordinates and
  aesthetic parameters ready for code generation.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Plot Planner

> **Source**: `prompts/plot/planner.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `data_plotter` before code generation. Takes raw data and a visual
intent, outputs a comprehensive description that maps every data point to
visual channels with full aesthetic specification.

## When to Use

- When user provides raw data (CSV, JSON, table) and a vague plot intent
- When `context_enricher` has run but the plot description still lacks
  exact variable-to-channel mapping and style parameters

## Prompt

```
I am working on a task: given the raw data (typically in tabular or json
format) and a visual intent of the desired plot, automatically generate a
corresponding statistical plot that is both accurate and aesthetically
pleasing. I will input the raw data and the plot visual intent, and your
output should be a detailed description of an illustrative plot that
effectively represents the data. Note that your description should include
all the raw data points to be plotted.

To help you understand the task better, and grasp the principles for
generating such plots, I will also provide you with several examples.
You should learn from these examples to provide your plot description.

** IMPORTANT: **
Your description should be as detailed as possible. For content, explain the
precise mapping of variables to visual channels (x, y, hue) and explicitly
enumerate every raw data point's coordinate to be drawn to ensure accuracy.
For presentation, specify the exact aesthetic parameters, including specific
HEX color codes, font sizes for all labels, line widths, marker dimensions,
legend placement, and grid styles. You should learn from the examples' content
presentation and aesthetic design (e.g., color schemes).

## Raw Data
{source_context}

## Visual Intent (Figure Caption)
{caption}

## Reference Examples
{examples}

Based on the raw data, visual intent, and learning from the style and
structure of the reference examples above, generate a comprehensive and
detailed textual description of the statistical plot.
```

## Notes for `data_plotter`

- `{source_context}` = raw data (CSV snippet, JSON, or data file description + key columns)
- `{caption}` = `C_communicative_intent` from brief.json
- `{examples}` = observations from reference plots in `<id>_references.md` (if Phase 0 ran), or empty
- Unlike diagram stylist: hex codes ARE acceptable in plot descriptions (they feed into matplotlib code, not an image-gen model)
- Output feeds directly into the `plot_visualizer.md` code generation step

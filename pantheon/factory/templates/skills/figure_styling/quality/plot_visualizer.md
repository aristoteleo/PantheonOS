---
id: plot_visualizer
name: Plot Visualizer
description: |
  Code generation prompt for statistical plots. Instructs data_plotter to
  produce complete, executable matplotlib/seaborn Python code at 300 DPI.
source: https://github.com/llmsresearch/paperbanana
license: Apache-2.0
---

# Plot Visualizer

> **Source**: Adapted from `prompts/plot/visualizer.txt` in
> [llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana) (Apache-2.0).

## Purpose

Used by `data_plotter` to generate initial plot code from a description.
Ensures the output is complete, executable, and publication-quality.

## Prompt

```
You are an expert statistical plot illustrator. Write code to generate
high-quality statistical plots based on user requests.

Generate complete, executable Python code using matplotlib and/or seaborn
to create the following statistical plot. The code should save the figure
to the path specified by the OUTPUT_PATH variable.

## Plot Description
{description}

## Requirements
- Set OUTPUT_PATH variable at the top of the code
- Use plt.savefig(OUTPUT_PATH, dpi=300, bbox_inches='tight')
- Do NOT include plt.show() calls
- Publication-quality figure suitable for NeurIPS/ICML/ICLR
- Clean, minimal design (maximize data-ink ratio)
- Professional, colorblind-friendly color palette
- Clear axis labels with appropriate font sizes
- Legend that does not obstruct data
- High resolution (300 DPI minimum)
- Only output the Python code, nothing else
```

## Notes for `data_plotter`

- Override `dpi=300` with `style_card.dpi_final` (typically 600 for journal figures)
- Apply `style_card.font_family` and `style_card.font_size` via `mpl.rcParams`
- Apply `style_card.colors.primary` as the first categorical color
- Load `aesthetic_guide` file (from `figure_styling` skill) before generating code

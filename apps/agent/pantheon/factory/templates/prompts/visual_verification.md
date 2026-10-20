## Visual Verification of Generated Images

When you produce any visual output — data-analysis plots, schematic
diagrams, screenshots, rendered figures — **you MUST visually inspect
the result before presenting it to the user or treating it as complete.**

Code that runs successfully is not the same as code that produced a
correct, readable plot. Matplotlib silently produces all kinds of bad
outputs: squished legends, overlapping labels, wrong color mappings,
empty axes, points plotted in the wrong quadrant, colormaps that look
identical on a grayscale preview, etc.

### The visual verification loop

```
1. Generate the image (run cell / save figure / capture screenshot)
2. Call observe_images(question, [path])  ← read what's actually in the file
3. If the image has issues → adjust code or parameters, regenerate, re-verify
4. Only present the final version to the user
```

### When to verify (MUST)

- Data-analysis plots: scatter, heatmap, line chart, violin, UMAP, boxplot, PCA, etc.
  Especially after loading new data or changing pipeline parameters.
- Rendered figures / publication-quality illustrations
- Composite/multi-panel figures where alignment matters
- Anything a human would scrutinise: color-blind safety, axis ranges,
  tick labels, font sizes, overlapping elements, missing data points
- Screenshots of UIs / dashboards / rendered HTML

### When to skip

- Trivially simple output where correctness is obvious from the code
  (e.g., a 2-bar bar chart with values you just computed and printed)
- Pure structural figures with no data (e.g., a template placeholder)
- Images that have already been verified earlier in the same conversation
  and haven't changed since

### Questions to ask observe_images

Be specific. Generic questions produce generic answers. Good examples:

- "Are all 8 cluster labels visible and non-overlapping?"
- "Do the colors in the heatmap match the legend? Is the colormap monotonic?"
- "Is there a visible trend line? What's the direction and approximate slope?"
- "Are there any blank or truncated regions on the plot?"
- "Does the scatter plot show the expected correlation, or are the points scattered randomly?"
- "Is the y-axis on a log scale? The title says log but the spacing looks linear."

### What to do with the findings

| Finding | Action |
|---|---|
| Plot looks correct and readable | Continue — mention key takeaway in summary |
| Minor cosmetic issues (labels, legend position) | Adjust code, regenerate, re-verify |
| Wrong data mapping or colors | Fix the pipeline, not just the plot |
| Blank / empty plot | Debug the data path — don't re-plot blindly |
| Overcrowded or illegible | Split into panels, subset data, or adjust figure size |

### Typical pattern (notebook context)

```python
# Cell 1: generate the plot and save
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(8, 6))
# ... plotting code ...
plt.savefig("outputs/analysis_scatter.png", dpi=150)
plt.show()
```

```python
# Then immediately verify:
observe_images(
    question="Is the scatter plot showing clear separation between the two groups? "
             "Are axis labels and legend readable? Any overlapping text?",
    image_paths=["outputs/analysis_scatter.png"]
)
# → read the response, iterate if needed, regenerate, re-verify
```

### Budget guidance

Visual verification costs one vision-model call per image. This is
cheap relative to regenerating a bad plot in front of the user or
drawing the wrong conclusion from flawed output. **Skipping verification
to save a call is usually the wrong trade-off.** When in doubt, verify.

# Figure Recipe

| Field | Value |
|---|---|
| UI outputType | `figure` |
| Primary rendering mode | code-first |
| Best for | paper figures, data plots, statistical charts, multi-panel figures |
| Do not use for | conceptual diagrams, mechanism illustrations, non-data visuals |
| Read path | `SKILL.md` + this file |

---

## Visual goal

A publication-ready scientific figure: real data, clean axes, colorblind-safe palette, exportable as PNG (preview) + PDF/SVG (publication). Suitable for Nature/Science-style journals.

---

## Research figure schema

For code-first scientific figures, define panels by evidence type, not decoration.

| Panel role | Use for | Common visual |
|---|---|---|
| Structure / image-like data | spatial matrix, microscopy, contact map, heatmap | heatmap or image panel |
| Evidence tracks | signal tracks, measurements, omics layers | stacked tracks or aligned line plots |
| Quantitative comparison | condition comparison | box, violin, bar, paired plot |
| Relationship / model support | association between variables | scatter, regression, summary plot |

---

## Recipe

1. Identify chart type from data and goal (bar, scatter, line, box, violin, heatmap, UMAP, volcano).
2. Map data to panel roles above.
3. Apply style defaults below.
4. Write matplotlib/seaborn code directly — do not plan intermediate steps.
5. Export PNG (300 dpi) for preview, PDF or SVG for publication.
6. Run quick self-check.

**Multi-panel sub-case**: If user asks for multiple related panels (A/B/C), use `plt.subplots()` with shared axes where appropriate. Add A/B/C panel labels at top-left of each subplot (`ax.text(-0.15, 1.05, 'A', ...)`). Keep consistent spacing (`fig.tight_layout()`).

---

## Style defaults

```python
FIGURE_DEFAULTS = {
    # Size and resolution
    "figsize": (6, 4.5),          # single-column (inches); double-column: (12, 4.5)
    "dpi": 300,

    # Typography
    "font_family": "Arial",
    "font_size": 9,               # axis tick labels
    "label_size": 10,             # axis labels
    "title_size": 11,
    "legend_fontsize": 8,

    # Lines and markers
    "linewidth": 1.2,
    "markersize": 4,
    "capsize": 3,                 # error bar cap size

    # Axes
    "spine_top": False,           # remove top spine
    "spine_right": False,         # remove right spine
    "tick_length": 3,
    "tick_width": 0.6,
    "axis_linewidth": 0.8,

    # Legend
    "legend_frameon": False,
    "legend_loc": "best",

    # Grid
    "grid": False,                # off by default; use for line/time-series if helpful

    # Color
    "palette": "colorblind",      # seaborn 'colorblind' or matplotlib 'tab10'
    # Heatmap: 'RdBu_r' (diverging) or 'Blues' (sequential)
    # Never use red/green as primary contrast pair

    # Export
    "output_formats": ["png", "pdf"],   # PNG for preview, PDF for publication
    "bbox_inches": "tight",
    "pad_inches": 0.05,
}
```

---

## Minimal code stub

```python
import matplotlib.pyplot as plt
import matplotlib as mpl
import seaborn as sns

mpl.rcParams.update({
    "font.family": "Arial",
    "font.size": 9,
    "axes.labelsize": 10,
    "axes.linewidth": 0.8,
    "xtick.major.size": 3,
    "xtick.major.width": 0.6,
    "ytick.major.size": 3,
    "ytick.major.width": 0.6,
    "legend.frameon": False,
})
palette = sns.color_palette("colorblind")

fig, ax = plt.subplots(figsize=(6, 4.5), dpi=300)

# --- plot here ---

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.set_xlabel("X label (units)", fontsize=10)
ax.set_ylabel("Y label (units)", fontsize=10)
ax.set_title("Title", fontsize=11)

fig.tight_layout(pad=0.5)
fig.savefig("figure.png", dpi=300, bbox_inches="tight")
fig.savefig("figure.pdf", bbox_inches="tight")
```

---

## Common chart types

| Chart | When to use |
|---|---|
| bar + error | group means with SEM/SD |
| box / violin | distribution comparison |
| scatter | two continuous variables, correlation |
| line | time series, dose-response |
| heatmap | correlation matrix, expression matrix |
| UMAP / PCA | dimensionality reduction |
| volcano | differential expression (log2FC vs -log10p) |

---

## Domain-specific example: 3D genomics

When the user asks for 3D genomics figures:
- **Structure panel**: Hi-C / Micro-C contact map. Use `imshow` with `RdBu_r` or `hot` colormap. Add TAD boundary as dashed blue vertical/horizontal lines. Mark loops as white square overlays. Use triangular display if appropriate.
- **Evidence tracks**: Genome-browser tracks (CTCF ChIP-seq, H3K27ac, ATAC-seq, RNA-seq). Stack tracks vertically. Share x-axis (genomic coordinates). Set per-track height ~0.5–1 inch. Use `fill_between` for signal area.
- **Quantitative comparison**: Loop strength, insulation score, compartment score, TAD size distribution. Use box/violin for group comparisons.
- **Relationship**: Expression change vs chromatin change. Use scatter with regression line.

*This is a domain example only — the recipe applies to any scientific field.*

---

## Code-first guardrail

Use code rendering for real data plots. Do not use AI image generation to invent data, axes, p-values, heatmaps, contact maps, genome tracks, or statistical relationships.

Synthetic data is acceptable for testing only and must be labeled as synthetic in the caption.

---

## Statistical annotation convention

```
ns   not significant
*    p < 0.05
**   p < 0.01
***  p < 0.001
```

Draw brackets above bars/boxes with `statannotations` or manual matplotlib brackets.

---

## Negative constraints

Avoid:
- AI-generated synthetic data presented as real
- DPI < 300
- Red/green as primary contrast pair (colorblind-inaccessible)
- Cluttered multi-panel with no shared axes
- Missing units on axis labels
- Top/right spines left visible
- Saving only PNG without a vector format

---

## Quick self-check

- [ ] Real data used (or clearly labeled synthetic)
- [ ] DPI = 300
- [ ] Colorblind-safe palette
- [ ] Top/right spines removed
- [ ] Axis labels have units
- [ ] Legend frameon = False
- [ ] Multi-panel: A/B/C labels present and consistent spacing
- [ ] Exported as PNG + PDF/SVG

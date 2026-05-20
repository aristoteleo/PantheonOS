# NeurIPS 2025 Statistical Plot Aesthetic Guideline

> **Attribution**: This guideline is adapted from the PaperBanana project
> ([github.com/dwzhu-pku/PaperBanana](https://github.com/dwzhu-pku/PaperBanana),
> Apache-2.0), specifically `style_guides/neurips2025_plot_style_guide.md`.
> It was automatically synthesized from NeurIPS 2025 publications via
> Zhu et al., *PaperBanana: Automating Academic Illustration for AI
> Scientists*, arXiv:2601.23265. Use this as a reference for
> publication-quality statistical plots and charts.

## 1. High-Level Overview

The prevailing aesthetic is defined by **precision, accessibility, and high contrast**. The look has shifted from bare-bones styling toward graphic, publication-ready presentation.

- **Vibe**: professional, clean, information-dense
- **Backgrounds**: stark white for maximum contrast (Seaborn-style light grey is accepted)
- **Accessibility**: distinguish data by texture (patterns) and shape (markers), not just color — support black-and-white printing and colorblind readers

## 2. Detailed Style Options

### Color Palettes

**Categorical data**
- **Soft pastels**: matte, low-saturation (salmon, sky blue, mint, lavender) to prevent visual fatigue
- **Muted earth tones**: olive, beige, slate grey, navy — the "academic" palette
- **High-contrast primaries**: sparingly, when categories must be sharply distinct (e.g., deep orange vs. vivid purple)
- **Accessibility mode**: combine color with **geometric patterns** (hatches, dots, stripes)

**Sequential & heatmaps**
- **Perceptually uniform**: viridis (blue-to-yellow), magma/plasma (purple-to-orange)
- **Diverging**: coolwarm (blue-to-red) for positive/negative splits
- **Avoid**: Jet/Rainbow is nearly extinct in modern venues

### Axes & Grids

- **Grid**: never solid. Use fine dashed (`--`) or dotted (`:`) in light grey, rendered *behind* data.
- **Spines**: "boxed" (all 4 sides) for formal style; "open" (remove top + right) for minimal style.
- **Ticks**: subtle, inward-facing, or removed entirely in favor of grid alignment.

### Layout & Typography

- **Font family**: exclusively **sans-serif** (Helvetica, Arial, DejaVu Sans). Serif for axes is considered outdated.
- **Label rotation**: 45° only when necessary to prevent overlap; horizontal preferred.
- **Legends**: float *inside* the plot area (top-left/right) to maximize data-ink ratio; or a single row above the plot.
- **Annotations**: **direct labeling** (text next to lines / on bars) is preferred over forcing readers to match a legend.

## 3. Type-Specific Guidelines

### Bar Charts & Histograms
- **Borders**: (a) **high-definition** — black outlines for high-contrast, or (b) **borderless** — solid fill with no outline (common with light grey backgrounds)
- **Grouping**: bars tightly grouped, significant whitespace between categorical groups
- **Error bars**: black, flat caps, consistent width

### Line Charts
- **Markers**: always add **geometric markers** (circles, squares, diamonds) at data points — smooth lines without markers look ambiguous
- **Line styles**: solid for primary experimental data; dashed for baselines / theoretical limits / secondary data
- **Uncertainty**: semi-transparent **shaded bands** (confidence intervals) rather than simple error bars

### Tree & Pie / Donut Charts
- **Separators**: thick **white borders** between slices/blocks
- **Structure**: thick **donut charts** preferred over traditional pie
- **Emphasis**: "exploding" (detaching) a specific slice to highlight a key statistic

### Scatter Plots
- **Shape coding**: different marker shapes (circles vs. triangles) to encode categorical dimension alongside color
- **Fills**: markers solid and fully opaque
- **3D plots**: "walls" with grids or drop-lines to the "floor" for depth emphasis

### Heatmaps
- **Aspect ratio**: cells almost strictly **square**
- **Annotation**: exact value in white/black text **inside the cell** is preferred over relying solely on a color bar
- **Borders**: borderless (smooth gradient) or very thin white lines between cells

### Radar Charts
- **Fills**: polygon area uses **translucent fills** (alpha ~0.2) to show grid underneath
- **Perimeter**: solid, darker line for the outer boundary

### Miscellaneous
- **Dot plots** ("lollipops"): modern alternative to bar charts — dots connected to axis by thin lines

## 4. Common Pitfalls (What to Avoid)

- ❌ **"Excel default" look**: heavy 3D effects on bars, drop shadows, serif fonts on axes
- ❌ **Rainbow colormap**: Jet/Rainbow is perceptually misleading and outdated
- ❌ **Missing markers**: line chart without markers looks ambiguous with sparse data
- ❌ **Color-only differentiation**: failing to use patterns/shapes makes plots inaccessible to colorblind readers
- ❌ **Heavy grids**: solid black grid lines compete with data — always use light grey/dashed

## 5. Recommended matplotlib rcParams Baseline

```python
from cycler import cycler
import matplotlib as mpl

# Paul Tol bright — colorblind-safe default (from color_palettes.md)
BRIGHT = ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"]

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 9,
    "axes.labelsize": 10,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.titlesize": 11,
    "axes.linewidth": 0.8,
    "lines.linewidth": 1.5,
    "lines.markersize": 5,

    # Color cycle — Paul Tol bright
    "axes.prop_cycle": cycler("color", BRIGHT),

    # Grid — dashed, light, behind data
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
    "grid.color": "#cccccc",

    # Spines — open (top + right removed)
    "axes.spines.top": False,
    "axes.spines.right": False,

    # Ticks — inward, minor ticks on bottom+left only
    "xtick.direction": "in",
    "xtick.major.size": 3,
    "xtick.major.width": 0.5,
    "xtick.minor.size": 1.5,
    "xtick.minor.width": 0.5,
    "xtick.minor.visible": True,
    "ytick.direction": "in",
    "ytick.major.size": 3,
    "ytick.major.width": 0.5,
    "ytick.minor.size": 1.5,
    "ytick.minor.width": 0.5,
    "ytick.minor.visible": True,

    # Legend
    "legend.frameon": False,

    # Save
    "savefig.dpi": 600,
    "savefig.bbox": "tight",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})
```

This baseline can be overridden by the project-specific `style_card.json`.

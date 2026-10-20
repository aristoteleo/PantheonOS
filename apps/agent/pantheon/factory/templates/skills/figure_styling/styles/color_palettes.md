---
id: color_palettes
name: Scientific Color Palettes
description: |
  Publication-safe color palettes for scientific figures. All palettes are
  colorblind-accessible and print safely in grayscale. Based on Paul Tol's
  color schemes via SciencePlots (garrettj403/SciencePlots, MIT).
source: https://github.com/garrettj403/SciencePlots
license: MIT
---

# Scientific Color Palettes

> **Attribution**: All palettes from SciencePlots
> ([github.com/garrettj403/SciencePlots](https://github.com/garrettj403/SciencePlots), MIT),
> `scienceplots/styles/color/`. Original color schemes designed by
> Paul Tol ([personal.sron.nl/~pault](https://personal.sron.nl/~pault/)).

## When to Use Which Palette

| Palette | Best for | Categories | Colorblind-safe |
|---|---|---|---|
| `bright` | Most scientific plots | ≤7 | ✅ |
| `vibrant` | Presentation slides, posters | ≤7 | ✅ |
| `muted` | Dense multi-category, earth tones | ≤9 | ✅ |
| `high-vis` | Accessibility-critical, color-blind audiences | ≤7 | ✅ |
| `retro` | Distinctive / unique look | ≤6 | Partial |
| `std-colors` | Default matplotlib replacement | ≤7 | Partial |

**Rule**: always use colorblind-safe palette unless user explicitly overrides. ~8% of readers have color vision deficiency.

---

## Palette Definitions

### `bright` — Paul Tol Bright (recommended default)

> From `scienceplots/styles/color/bright.mplstyle`

```python
BRIGHT = ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"]
# Blue, Red, Green, Yellow, Cyan, Purple, Grey
```

```python
mpl.rcParams["axes.prop_cycle"] = cycler("color", [
    "#4477AA",  # blue
    "#EE6677",  # red
    "#228833",  # green
    "#CCBB44",  # yellow
    "#66CCEE",  # cyan
    "#AA3377",  # purple
    "#BBBBBB",  # grey
])
```

**Use with**: `science`, `nature_figure`, `neurips_plot` styles. Default recommendation for all CNS figures.

---

### `vibrant` — Paul Tol Vibrant

> From `scienceplots/styles/color/vibrant.mplstyle`

```python
VIBRANT = ["#EE7733", "#0077BB", "#33BBEE", "#EE3377", "#CC3311", "#009988", "#BBBBBB"]
# Orange, Blue, Cyan, Magenta, Red, Teal, Grey
```

```python
mpl.rcParams["axes.prop_cycle"] = cycler("color", [
    "#EE7733",  # orange
    "#0077BB",  # blue
    "#33BBEE",  # cyan
    "#EE3377",  # magenta
    "#CC3311",  # red
    "#009988",  # teal
    "#BBBBBB",  # grey
])
```

**Use with**: poster figures, slides, graphical abstracts — more vivid than `bright`.

---

### `muted` — Paul Tol Muted

> From `scienceplots/styles/color/muted.mplstyle`

```python
MUTED = ["#CC6677", "#332288", "#DDCC77", "#117733",
         "#88CCEE", "#882255", "#44AA99", "#999933", "#AA4499"]
# Rose, Indigo, Sand, Green, Cyan, Wine, Teal, Olive, Purple
```

```python
mpl.rcParams["axes.prop_cycle"] = cycler("color", [
    "#CC6677",  # rose
    "#332288",  # indigo
    "#DDCC77",  # sand
    "#117733",  # green
    "#88CCEE",  # cyan
    "#882255",  # wine
    "#44AA99",  # teal
    "#999933",  # olive
    "#AA4499",  # purple
])
```

**Use with**: figures with many categories (7–9). More subdued — appropriate for dense comparison plots.

---

### `high-vis` — High Visibility (Paul Tol)

> From `scienceplots/styles/color/high-vis.mplstyle`

```python
HIGH_VIS = ["#0077BB", "#33BBEE", "#009988", "#EE7733", "#CC3311", "#EE3377", "#BBBBBB"]
```

```python
mpl.rcParams["axes.prop_cycle"] = (
    cycler("color", ["#0077BB", "#33BBEE", "#009988",
                     "#EE7733", "#CC3311", "#EE3377", "#BBBBBB"]) +
    cycler("ls", ["-", "--", "-.", ":", "-", "--", "-."])
)
# lines.linewidth: 1.5 (thicker for visibility)
mpl.rcParams["lines.linewidth"] = 1.5
```

**Use with**: accessibility-critical contexts. Line style variation built in for B&W printing.

---

### `retro` — Retro

> From `scienceplots/styles/color/retro.mplstyle`

```python
RETRO = ["#4165c0", "#e770a2", "#5ac3be", "#696969", "#f79a1e", "#ba7dde"]
# Blue, Pink, Cyan, Grey, Orange, Purple
```

```python
mpl.rcParams["axes.prop_cycle"] = cycler("color", [
    "#4165c0",  # blue
    "#e770a2",  # pink
    "#5ac3be",  # cyan
    "#696969",  # grey
    "#f79a1e",  # orange
    "#ba7dde",  # purple
])
```

**Use with**: distinctive aesthetic preference. Less common in CNS-type journals; more suited to preprints, blogs, data journalism.

---

### `std-colors` — Standard Scientific Colors

```python
STD_COLORS = ["#0C5DA5", "#00B945", "#FF9500", "#FF2C00", "#845B97", "#474747", "#9e9e9e"]
# Blue, Green, Orange, Red, Purple, Dark Grey, Grey
```

This is the SciencePlots `science.mplstyle` default cycle — used by most of its journal styles including `nature.mplstyle` as the base before font/size overrides. A clean, professional look for general scientific use.

---

## Application

Set the active style configuration's categorical palette to the desired array:

```json
{
  "colors": {
    "categorical_palette": ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377"],
    "diverging_cmap": "RdBu_r",
    "sequential_cmap": "viridis"
  }
}
```

Plotting code should read the configured categorical palette and use it as the `axes.prop_cycle`.

## Combining Palettes with Style Files

SciencePlots design: palettes are **modular overlays**, not standalone styles.

```python
# Correct usage pattern:
plt.style.use(["science"])          # base style (sizes, ticks, fonts)
plt.style.use(["bright"])           # color overlay
# Or combined:
plt.style.use(["science", "bright"])
```

Recommended composition:
- Base style file (neurips_plot / nature_figure / ieee_figure) sets typography, axes, ticks, and export defaults
- Categorical palette provides the color overlay
- Plotting code applies both independently

## Sequential & Diverging (not cycler-based)

| Use case | Colormap | Notes |
|---|---|---|
| Ordered data, heatmap | `viridis` | Default, perceptually uniform |
| Intensity / density | `magma` or `plasma` | High contrast at extremes |
| Positive / negative | `RdBu_r` | Diverging, white at zero |
| Gene expression | `RdYlBu_r` or `PuOr` | Biology convention |
| Genomics / ATAC | `Blues` or `YlOrRd` | Single-hue sequential |
| **Avoid** | `jet`, `rainbow`, `hot` | Perceptually non-uniform, misleading |

---
id: nature_figure
name: "Nature / Cell / Science Figure Style"
description: |
  Aesthetic guidelines for publication-quality figures targeting Nature,
  Cell, Science, and their family journals. Based on SciencePlots
  (garrettj403/SciencePlots, MIT) nature.mplstyle, with Cell Press and
  AAAS Science size specifications added.
source: https://github.com/garrettj403/SciencePlots
license: MIT
---

# Nature / Cell / Science Figure Style

> **Attribution**: rcParams baseline from SciencePlots
> ([github.com/garrettj403/SciencePlots](https://github.com/garrettj403/SciencePlots), MIT),
> `scienceplots/styles/journals/nature.mplstyle`. Extended with Cell Press
> and AAAS Science journal specifications from author guidelines.

## 1. The "CNS Look"

Three journals — Cell, Nature, Science — share a house style:
- **7 pt body text** — the single most distinctive constraint; everything is small and precise
- **Sans-serif throughout** — Arial/Helvetica exclusively (DejaVu Sans as fallback)
- **No gridlines** — stark white background, no grid in most figure types
- **Inward ticks on all 4 sides** — top and right ticks on, minor ticks visible
- **Muted colorblind-safe palettes** — never Jet/Rainbow; prefer Paul Tol sets (see `color_palettes.md`)
- **600 DPI final** — 300 DPI preview acceptable

## 2. rcParams Baseline (from `nature.mplstyle`)

```python
import matplotlib as mpl

mpl.rcParams.update({
    # Figure size — Nature single column (max 3.5" wide)
    "figure.figsize": [3.5, 2.625],

    # Fonts — sans-serif, 7 pt body
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7,
    "axes.labelsize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.titlesize": 8,
    "mathtext.fontset": "dejavusans",

    # Ticks — inward, all 4 sides, minor ticks on
    "xtick.direction": "in",
    "xtick.major.size": 3,
    "xtick.major.width": 0.5,
    "xtick.minor.size": 1.5,
    "xtick.minor.width": 0.5,
    "xtick.minor.visible": True,
    "xtick.top": True,
    "ytick.direction": "in",
    "ytick.major.size": 3,
    "ytick.major.width": 0.5,
    "ytick.minor.size": 1.5,
    "ytick.minor.width": 0.5,
    "ytick.minor.visible": True,
    "ytick.right": True,

    # Lines
    "axes.linewidth": 0.5,
    "grid.linewidth": 0.5,
    "lines.linewidth": 1.0,
    "lines.markersize": 3,

    # No legend frame
    "legend.frameon": False,

    # Save
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.01,
    "savefig.dpi": 600,
    "pdf.fonttype": 42,   # editable text in PDF
    "ps.fonttype": 42,
    "svg.fonttype": "none",  # editable text in SVG
})
```

## 3. Figure Sizes by Journal

| Journal | Single column | 1.5-column | Double column | Notes |
|---|---|---|---|---|
| **Nature family** | 3.5" × 2.625" | — | 7.2" × 5.0" | Max width 3.5" single |
| **Cell Press** | 3.35" × 2.5" (85 mm) | 4.49" (114 mm) | 6.85" × 5.0" (174 mm) | Height flexible |
| **AAAS Science** | 2.24" × 2.0" (57 mm) | 4.72" (120 mm) | 4.72" × 3.5" | Very narrow single |
| **Default / unknown** | 3.5" × 2.625" | — | 7.0" × 5.0" | Safe fallback |

**Graphical abstract sizes**:
- Cell Press: 169 mm × 60 mm (6.65" × 2.36") landscape
- Nature: 90 mm × 60 mm (3.54" × 2.36") portrait-ish
- Elsevier: 130 mm × 70 mm (5.12" × 2.76")

## 4. Recommended Color Palettes

See `color_palettes.md` for the full Paul Tol palette library. Quick reference for CNS:

**Categorical (colorblind-safe)**:
```python
# Paul Tol "bright" — recommended default for CNS
BRIGHT = ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE", "#AA3377", "#BBBBBB"]

# Paul Tol "muted" — for dense multi-category plots
MUTED = ["#CC6677", "#332288", "#DDCC77", "#117733", "#88CCEE", "#882255", "#44AA99"]
```

**Sequential / heatmap**:
- `viridis` — default for all sequential data
- `magma` / `plasma` — alternative perceptually uniform
- `RdBu_r` — diverging (positive/negative splits)
- Never `jet` / `rainbow`

## 5. Panel Letters

Nature style panel letters: **bold, 8 pt, upper-left corner** of each panel.

```python
ax.text(-0.15, 1.05, "a", transform=ax.transAxes,
        fontsize=8, fontweight="bold", va="top", ha="right")
```

Cell Press uses lower-case bold: `a`, `b`, `c` at 8 pt.

## 6. Multi-panel Figure Rules

- All panels must use **identical font sizes** across a figure
- **Align panel bottoms** on a common baseline (use `gridspec` with `hspace`)
- **No whitespace waste** — pack panels tightly; Nature reviewers notice gaps
- **Consistent axis ranges** when comparing conditions across panels
- **Scale bars** instead of axis ticks for microscopy / spatial data

## 7. Common Pitfalls

- ❌ Font > 7 pt — wastes precious column width; reviewers notice
- ❌ Gridlines — CNS almost never shows them in main figures
- ❌ Both top/right spines visible but un-ticked — either add ticks or remove spines
- ❌ Serif fonts on axes — DejaVu Sans, not Times
- ❌ Figure wider than column spec — journal production will resize, destroying aspect ratio
- ❌ Jet/Rainbow colormap — unacceptable in all three journals
- ❌ Panel letters not bold — must be bold weight
- ❌ Caption text inside figure — captions go in `figure_legends.md`

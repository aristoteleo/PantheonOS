---
id: ieee_figure
name: IEEE Figure Style
description: |
  Aesthetic guidelines for statistical plots targeting IEEE journals and
  conferences (TPAMI, CVPR, ICCV, ICASSP, etc.). Based on SciencePlots
  (garrettj403/SciencePlots, MIT) ieee.mplstyle. Black-and-white compatible,
  600 DPI, Computer Modern serif, inward ticks with minor ticks visible.
source: https://github.com/garrettj403/SciencePlots
license: MIT
---

# IEEE Figure Style

> **Attribution**: rcParams baseline directly from SciencePlots
> ([github.com/garrettj403/SciencePlots](https://github.com/garrettj403/SciencePlots), MIT),
> `scienceplots/styles/journals/ieee.mplstyle`.
> Original content: `axes.prop_cycle : cycler('color', ['k', 'r', 'b', 'g']) +
> cycler('ls', ['-', '--', ':', '-.'])`, figure.figsize 3.5×2.625,
> font.family serif, font.serif cmr10/Computer Modern.

## 1. The "IEEE Look"

IEEE figures are defined by:
- **Black-and-white compatibility first** — every series distinguished by line style, not just color
- **Computer Modern serif** — cmr10 (same as LaTeX default), not Times New Roman
- **Inward ticks on all 4 sides** — top + right ticks on, minor ticks visible
- **No legend frame** — frameon = False
- **3.5" single column** — slightly wider than Nature (3.3")

## 2. rcParams Baseline (from `ieee.mplstyle`)

```python
from cycler import cycler
import matplotlib as mpl

mpl.rcParams.update({
    # Color + line style + marker cycle — B&W compatible, ≥6 series distinguishable
    # Source: scienceplots/styles/journals/ieee.mplstyle (color+ls) + marker added
    "axes.prop_cycle": (
        cycler("color", ["k", "r", "b", "g", "m", "c"]) +
        cycler("ls", ["-", "--", ":", "-.", "-", "--"]) +
        cycler("marker", ["o", "s", "^", "D", "v", "P"])
    ),

    # Figure size — 3.5" single column
    "figure.figsize": [3.5, 2.625],

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

    # Line widths
    "axes.linewidth": 0.5,
    "grid.linewidth": 0.5,
    "lines.linewidth": 1.0,
    "lines.markersize": 4,  # small markers for dense plots

    # Font — Computer Modern serif (LaTeX default)
    "font.family": "serif",
    "font.serif": ["cmr10", "Computer Modern Serif", "DejaVu Serif"],
    "axes.formatter.use_mathtext": True,
    "mathtext.fontset": "cm",
    "font.size": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,

    # No legend frame
    "legend.frameon": False,

    # Save
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
    "savefig.dpi": 600,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
})
```

## 3. Figure Sizes

| Format | Width × Height | Notes |
|---|---|---|
| Single column | 3.5" × 2.625" | Standard |
| 1.5-column | 5.0" × 3.5" | Some journals allow |
| Double column | 7.16" × 5.0" | Full-width |

## 4. Color & Line Style Strategy

**Primary rule**: every series must be distinguishable in grayscale print.

The `axes.prop_cycle` in the rcParams baseline above pairs **color + line style + marker** automatically for up to 6 series. This triple encoding ensures B&W print, colorblind, and screen viewers can all distinguish data series.

- Series 1: black solid circle `k-o`
- Series 2: red dashed square `r--s`
- Series 3: blue dotted triangle-up `b:^`
- Series 4: green dash-dot diamond `g-.D`
- Series 5: magenta solid triangle-down `m-v`
- Series 6: cyan dashed plus `c--P`

**For heatmaps**: grayscale (`plt.cm.Greys`) or single-hue sequential. Avoid multi-hue.

## 5. Font Notes

IEEE LaTeX templates use Computer Modern (`\usepackage{times}` is common but CM is acceptable). The `cmr10` entry in `font.serif` ensures matplotlib uses Computer Modern when available. If not installed, falls back to DejaVu Serif — acceptable for drafts.

## 6. Common Pitfalls

- ❌ Color as sole differentiator — IEEE prints/reviews in B&W
- ❌ Sans-serif axis labels — CM serif is the house style
- ❌ Missing minor ticks — all 4 sides should show both major and minor
- ❌ Legend frame (`frameon=True`) — remove it
- ❌ Figure wider than 3.5" single column — will be resized by production
- ❌ Missing axis units — always `Accuracy (%)` not just `Accuracy`

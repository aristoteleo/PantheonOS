---
id: visual_quality_checklist
name: Visual Quality Checklist (Nature/Cell Level)
description: |
  Publication-readiness checklist for scientific figures targeting Nature,
  Cell, Science, and top ML venues. Covers data-ink ratio, typography,
  color, layout, and accessibility. Derived from rougier/scientific-
  visualization-book (CC BY-NC-SA) and Tufte's visual design principles.
source: https://github.com/rougier/scientific-visualization-book
license: CC BY-NC-SA 4.0
---

# Visual Quality Checklist (Nature / Cell Level)

> **Attribution**: Principles adapted from *Scientific Visualization: Python + Matplotlib*
> by Nicolas P. Rougier ([github.com/rougier/scientific-visualization-book](https://github.com/rougier/scientific-visualization-book),
> CC BY-NC-SA 4.0), and Edward Tufte's *The Visual Display of Quantitative Information*.
> Applied to the context of Nature/Cell/Science submission quality.

## Purpose

Used by `illustrator` (Phase 4 critic) and `data_plotter` (review loop) as a
**supplementary quality gate** on top of `diagram_critic.md` / `plot_critic.md`.
Run this checklist before reporting a figure as publication-ready.

---

## Tier 1 — Data Integrity (Hard failures — ALWAYS check)

These failures mean the figure is **scientifically misleading**. Reject and regenerate.

- [ ] **Axis zero baseline**: bar charts / area charts MUST start at zero unless data range makes it meaningless (e.g., 99.0–99.8% accuracy). Truncated Y axes misrepresent effect sizes.
- [ ] **Error bar definition stated**: if error bars exist, the caption must specify what they represent (SD, SEM, 95% CI, range). Critic flags if ambiguous.
- [ ] **No dual Y-axis distortion**: dual Y-axis plots are acceptable only when both scales have a principled relationship. Arbitrary scaling to make lines "appear" correlated is misleading.
- [ ] **Sample size visible**: N must appear in panel, legend, or caption. A beautiful figure with N=3 is still publishable; a figure where N is hidden is not.
- [ ] **Color encodes one dimension only**: a single color channel should not simultaneously encode two variables (e.g., both condition and significance level).

---

## Tier 2 — Data-Ink Ratio (Rougier / Tufte principle)

Every pixel in a figure should carry information. Remove everything that does not.

- [ ] **No chartjunk**: 3D perspective bars, drop shadows, gradient fills, decorative borders — all removed. None of these add information.
- [ ] **No redundant legend**: if data series are directly labeled on the plot (direct annotation), the floating legend is redundant — remove it.
- [ ] **No grid clutter**: gridlines, if present, must be light grey and dashed (never solid black). For CNS figures, gridlines are usually absent entirely.
- [ ] **Minimal spines**: either "boxed" (all 4 sides, formal) or "open" (top + right removed, modern). Never mix arbitrarily.
- [ ] **No unnecessary tick marks**: if a value is not referenced in the text or caption, its tick mark adds noise. Prune to the meaningful values.
- [ ] **Axis labels are informative**: `Accuracy (%)` not `acc`; `Time (s)` not `t`; `Expression level (log2 CPM)` not `expr`.

---

## Tier 3 — Typography (Rougier hierarchy principle)

Font hierarchy must be strictly enforced — readers use size to judge importance.

- [ ] **Strict font size hierarchy**: title > axis label > tick label > legend > annotation. Specifically for CNS: title (if any) 8 pt, axis labels 7 pt, ticks 7 pt, legend 7 pt. No two elements the same importance should be the same size.
- [ ] **Single font family throughout**: one figure, one family. Mixing Arial on axes with Times in legend is a common error.
- [ ] **No font below 6 pt**: anything smaller is unreadable in print. Scale figure or simplify.
- [ ] **Math in italic**: variables like *x*, *y*, *n*, *p* must be italic (LaTeX `$x$` or mathtext). Plain roman text `x` in a formula is typographically incorrect.
- [ ] **Units always present**: `[ms]`, `(%)`, `(log₂ FC)` — never omit units from quantitative axes.

---

## Tier 4 — Color & Accessibility

- [ ] **Colorblind-safe palette**: use Paul Tol `bright` or `vibrant` from `color_palettes.md`. Verify with a grayscale preview — all series must be distinguishable.
- [ ] **No Jet/Rainbow**: perceptually non-uniform, creates false visual cliffs. Replace with `viridis`, `magma`, or `RdBu_r`.
- [ ] **Color is not the only encoding**: for line charts, pair each color with a distinct marker shape; for bar charts, pair with hatch pattern when needed. This ensures B&W printing works.
- [ ] **Sufficient contrast**: text on colored backgrounds must pass WCAG AA (contrast ratio ≥ 4.5:1). White text on dark bars; black text on light bars.
- [ ] **Colorbar present for heatmaps**: every heatmap must have a visible colorbar with labeled min/max. Exception: small schematic heatmaps used purely for illustration.

---

## Tier 5 — Layout & Composition

- [ ] **Aspect ratio is principled**: don't stretch a figure to fill a column if the data doesn't warrant it. Square for heatmaps; 1.6:1 for most line/bar charts; 2:1 for wide comparison panels.
- [ ] **Panel letters are consistent**: all panels in a multi-panel figure use the same letter style — either all bold 8 pt top-left (Nature), or all bold 8 pt top-left (Cell). Never mix sizes.
- [ ] **White space is deliberate**: panels tightly packed but not touching. Use `hspace` and `wspace` in gridspec to control precisely.
- [ ] **Figure fits within column**: single-column figures ≤3.5" wide; double-column ≤7.2" wide. Figures that bleed into margins are rejected.
- [ ] **Insets are legible**: if an inset panel exists, its text must still meet minimum font size. Inset axes are typically 50–70% of the parent axes size.

---

## Tier 6 — Reproducibility (Nature/Cell submission requirement)

- [ ] **Data source referenced**: figure caption must indicate the data source (dataset name, accession number, or "n=X independent experiments").
- [ ] **Statistical test named**: "Two-sided unpaired t-test" not just "p < 0.05". Nature requires the specific test in Methods or figure caption.
- [ ] **Software version stated**: in Methods section, not in figure, but flagged here as a checklist item.
- [ ] **Code available**: for computational figures, code must be available (GitHub / Zenodo). Figures generated from code that cannot be shared will face reviewer questions.

---

## Critic Usage

`data_plotter` and `illustrator` run this checklist in their final review round.
Output format (append to critic JSON):

```json
{
  "visual_quality": {
    "tier1_data_integrity": "pass | fail | warning",
    "tier2_data_ink": "pass | fail | warning",
    "tier3_typography": "pass | fail | warning",
    "tier4_color": "pass | fail | warning",
    "tier5_layout": "pass | fail | warning",
    "tier6_reproducibility": "pass | N/A",
    "blockers": ["list of Tier 1 failures that must be fixed before delivery"],
    "warnings": ["list of Tier 2-5 issues that should be addressed"]
  }
}
```

**Tier 1 failures are hard blockers** — figure cannot be delivered until resolved.
**Tier 2–5 warnings** — flag to leader; leader decides whether to re-delegate or note in delivery.
**Tier 6** — informational; noted in `figure_legends.md` caption.

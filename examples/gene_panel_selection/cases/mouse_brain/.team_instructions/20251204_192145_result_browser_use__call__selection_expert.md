Done. I re-rendered all requested figures with the specified formatting and saved both PNG (300 dpi) and PDF versions using the original filenames plus the suffix _hires in:
cases/mouse_brain/workdir/selection_expert/figures/

Summary of updates
- Global styling across figures
  - Font family: DejaVu Sans; colorblind-friendly palette.
  - Sizes and layout per request (figure size ~10x7 for curves/boxplots, ~10x8 for UpSet-like, ~10x9 for UMAPs; constrained/tight layout).
  - Legends placed outside when appropriate.

- 1) ARI curves
  - File: cases/mouse_brain/workdir/selection_expert/figures/ari_curves_hires.png/pdf
  - Changes: legend outside (right), axis label fonts 14–16 pt, tick labels 12–14 pt, title ~18 pt, line width ~2.3, marker size ~7, colorblind palette, 10x7 in.

- 2) Benchmarking boxplots
  - Files:
    - cases/mouse_brain/workdir/selection_expert/figures/benchmark_ARI_boxplot_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/benchmark_NMI_boxplot_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/benchmark_SI_boxplot_hires.png/pdf
  - Changes: x tick labels rotated 90° (12–14 pt), axis labels 14–16 pt, title ~18 pt, thicker box/whisker lines, larger outliers, consistent y-axis limits (ARI/NMI 0–1; SI −1–1), constrained_layout=True, 10x7 in.

- 3) UpSet-like overlaps
  - File: cases/mouse_brain/workdir/selection_expert/figures/upset_like_overlaps_hires.png/pdf
  - Changes: increased matrix strip height for label legibility, label font ~12–14 pt, subtle grey for inactive cells to improve print contrast, improved spacing between bar and matrix, tight layout, 10x8 in.

- 4) UMAP comparisons
  - Files:
    - cases/mouse_brain/workdir/selection_expert/figures/umap_Full_HVG3k_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_HVG1000_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_DE1000_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_RF1000_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_scGeneFit1000_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_SpaPROS1000_hires.png/pdf
    - cases/mouse_brain/workdir/selection_expert/figures/umap_Final500_hires.png/pdf
  - Changes:
    - Colored points by subclass using a consistent palette across panels.
    - Labeled only cluster centroids (repelled with adjustText), font 10–12 pt.
    - Increased point size slightly (s≈7) and alpha≈0.6 to reduce overplotting.
    - Enforced identical UMAP axis limits across all panels for comparability.
    - Legend outside; figure size ~10x9 in; 300 dpi.

Note: I installed adjustText to enable centroid label repelling and used the combined_downsampled_50k embedding for consistent axes across panels.

Produced/updated files
- ARI curves:
  - cases/mouse_brain/workdir/selection_expert/figures/ari_curves_hires.png
  - cases/mouse_brain/workdir/selection_expert/figures/ari_curves_hires.pdf
- Benchmarking boxplots:
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_ARI_boxplot_hires.png
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_ARI_boxplot_hires.pdf
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_NMI_boxplot_hires.png
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_NMI_boxplot_hires.pdf
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_SI_boxplot_hires.png
  - cases/mouse_brain/workdir/selection_expert/figures/benchmark_SI_boxplot_hires.pdf
- UpSet-like overlaps:
  - cases/mouse_brain/workdir/selection_expert/figures/upset_like_overlaps_hires.png
  - cases/mouse_brain/workdir/selection_expert/figures/upset_like_overlaps_hires.pdf
- UMAP comparisons:
  - cases/mouse_brain/workdir/selection_expert/figures/umap_Full_HVG3k_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_HVG1000_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_DE1000_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_RF1000_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_scGeneFit1000_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_SpaPROS1000_hires.png/pdf
  - cases/mouse_brain/workdir/selection_expert/figures/umap_Final500_hires.png/pdf

If you want any further tweaks (e.g., slightly lower alpha on UMAP points, smaller centroid label font, or a separate color key panel to avoid legends entirely), I can update and re-save quickly.
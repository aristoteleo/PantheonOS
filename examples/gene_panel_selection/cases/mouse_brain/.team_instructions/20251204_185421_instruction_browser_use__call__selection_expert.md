Reporter agent here. We are finalizing the PDF report and need improved, publication-quality figures. Please re-render the following with the specified formatting and save both PNG (300 dpi) and PDF versions into selection_expert/figures/, keeping the original filenames but adding a suffix _hires (e.g., ari_curves_hires.png/pdf). Specific requests per figure:

1) ARI curves (selection_expert/figures/ari_curves.png)
- Place legend outside the plotting area (right side) or on a separate row below; use font 12–14 pt; reduce frame size.
- Axis label fonts 14–16 pt; tick labels 12–14 pt; title 18–20 pt.
- Increase line width to ~2.0–2.5 and marker size to ~6–8; use colorblind-friendly palette.
- Figure size ~10x7 in; tight/constrained layout.

2) Benchmarking boxplots (selection_expert/figures/benchmark_ARI_boxplot.png, benchmark_NMI_boxplot.png, benchmark_SI_boxplot.png)
- Rotate x tick labels to 90° with font 12–14 pt; axis labels 14–16 pt; title 18 pt.
- Increase box/whisker line width and outlier marker size.
- Add consistent y-axis limits across comparable metrics if applicable; constrained_layout=True.
- Figure size ~10x7 in.

3) UpSet-like overlaps (selection_expert/figures/upset_like_overlaps.png)
- Increase the matrix strip (black/white squares) height so labels are legible; label font ~12–14 pt.
- Improve spacing between the bar plot and matrix; use tight layout.
- Consider subtle grey for inactive cells to improve contrast when printed.
- Figure size ~10x8 in.

4) UMAP comparisons (selection_expert/figures/umap_Full_HVG3k.png, umap_HVG1000.png, umap_DE1000.png, umap_RF1000.png, umap_scGeneFit1000.png, umap_SpaPROS1000.png, umap_Final500.png)
- Critical: Do NOT label every point. Instead, color points by subclass (consistent palette across panels), and label only cluster centroids with repel (adjustText or similar) at font 10–12 pt.
- Increase point size slightly, add alpha ~0.6 to reduce overplotting.
- Ensure identical UMAP axis limits across panels for visual comparability.
- Place a single legend outside or create a separate color key figure (optional), but avoid per-point labels.
- Figure size ~10x9 in; 300 dpi.

5) General
- Save both PNG (300 dpi) and PDF versions with suffix _hires.
- Use consistent font family and colorblind-friendly palette.
- Confirm output paths after generation.

Once done, please reply with a short summary of the updated files you produced.
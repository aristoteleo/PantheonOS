Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Task: Replace the current UpSet-like overlap visualization with a proper UpSet plot showing intersections between the 500-gene method panels and the curated Final500 panel.

Context and goal:
- The report currently includes figures upset_like_overlaps_hires.(png|pdf). This is a hybrid membership matrix + set-size bar plot and is not a standard UpSet plot with intersection-size bars.
- The user specifically requested an UpSet graph for the 500-gene panels for each algorithm and the Final500 panel.

Inputs (already in your workdir):
- panel_hvg_500.tsv
- panel_de_500.tsv
- panel_rf_500.tsv
- panel_scgenefit_500.tsv
- panel_spapros_500.tsv
- gene_panels/final_receptor_centric_500.tsv

Requirements:
- Build a true UpSet plot for these six sets: HVG500, DE500, RF500, scGeneFit500, SpaPROS500, Final500.
- Use standard UpSet elements: set-size bars, intersection-size bars above the binary matrix, and the matrix dots/lines. Ensure readable labels and consistent styling with other figures.
- Save outputs:
  - Figures: selection_expert/figures/upset_500_true.png and upset_500_true_hires.(png|pdf)
  - Intersection counts table: selection_expert/results/intersections_upset_500.csv (include all non-empty intersections; also include simple Jaccard pairwise table if trivial to add: selection_expert/results/jaccard_pairs_500.csv)
- Optionally, include a small markdown snippet explaining how to read the UpSet plot and summarizing the main intersections; save to selection_expert/results/upset_500_notes.md.

Please do not recompute any panels; just consume the existing 500-gene lists. Keep runtime light.

After saving, please leave the existing upset_like_overlaps_* files untouched; we will just point the report to the new figure.
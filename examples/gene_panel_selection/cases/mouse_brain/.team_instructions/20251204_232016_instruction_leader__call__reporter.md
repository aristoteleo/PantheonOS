Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter

Goal: Regenerate an improved report.pdf in the project root incorporating:
- New 500-gene panel benchmarking (Final500 vs HVG500, DE500, RF500, scGeneFit500, SpaPROS500), using existing metrics and figures.
- Clean UMAPs (no over-annotation) for all panels, including both the 1000-gene baselines and the 500-gene method panels, with consistent aesthetics.
- Fixed recap table formatting to prevent method-column overflow (use the updated recap_longtable.tex already provided).

Inputs and paths (figures and tables are already created by selection_expert):
- 500-panel metrics CSVs:
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/benchmark_metrics_5subsets_500panels.csv
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/benchmark_metrics_summary_500panels.csv
- 500-panel benchmarking figures:
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/figures/benchmark_ARI_boxplot_500_hires.png
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/figures/benchmark_NMI_boxplot_500_hires.png
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert/figures/benchmark_SI_boxplot_500_hires.png
- Clean UMAP figures (use these instead of the older cluttered ones):
  * .../figures/umap_Full_HVG3k_clean_hires.png
  * .../figures/umap_HVG1000_clean_hires.png
  * .../figures/umap_RF1000_clean_hires.png
  * .../figures/umap_DE1000_clean_hires.png
  * .../figures/umap_SpaPROS1000_clean_hires.png
  * .../figures/umap_scGeneFit1000_clean_hires.png
  * .../figures/umap_Final500_clean_hires.png
  * .../figures/umap_HVG500_clean_hires.png
  * .../figures/umap_RF500_clean_hires.png
  * .../figures/umap_DE500_clean_hires.png
  * .../figures/umap_SpaPROS500_clean_hires.png
  * .../figures/umap_scGeneFit500_clean_hires.png
- Recap table TeX (already fixed):
  * /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter/recap_longtable.tex
- Keep other existing content/figures as in the current report, but:
  * Add a concise subsection highlighting the 500-gene benchmark with the three boxplots.
  * Replace the UMAP panel to use the *_clean_hires images; optionally add an additional figure mosaic for the five 500-gene method UMAPs.
  * Ensure the PDF compiles cleanly with the updated longtable.

Deliverable:
- Overwrite the root report.pdf with the corrected version.
- Leave brief changelog at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/reporter/CHANGELOG.md describing the updates (date-stamped).
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/selection_expert

Context
- Do NOT recompute any gene selection algorithms or curated panels. Use existing ranked lists/panels only.
- Environment: high-memory CPU, no GPU (see environment.md). Label key for evaluation: subclass (see selected_label_key.txt).
- Downsampled dataset to use: combined_downsampled_50k.h5ad. You previously used 5 stratified subsets; reuse the same splitting logic (consistent seed) for comparability.

Tasks
1) Add benchmarking for 500-gene method panels
- Use already exported 500-gene panels:
  * panel_hvg_500.tsv
  * panel_de_500.tsv
  * panel_rf_500.tsv
  * panel_scgenefit_500.tsv
  * panel_spapros_500.tsv
  * final_receptor_centric_500.tsv (Final500)
- Compute ARI, NMI, SI across the same 5 stratified subsets (subclass labels).
- Save metrics to:
  * benchmark_metrics_5subsets_500panels.csv
  * benchmark_metrics_summary_500panels.csv
- Produce boxplots (ARI/NMI/SI) comparing Final500 vs each 500-gene method panel (and include Full_HVG3k if you have it at 3k-only as a visual reference). Save to figures as:
  * benchmark_ARI_boxplot_500_hires.(png|pdf)
  * benchmark_NMI_boxplot_500_hires.(png|pdf)
  * benchmark_SI_boxplot_500_hires.(png|pdf)

2) Regenerate clean UMAPs for all panels
- For each panel used in the report (Full_HVG3k, HVG1000, RF1000, DE1000, SpaPROS1000, scGeneFit1000, Final500) and for the 500-gene method panels (HVG500, RF500, DE500, SpaPROS500, scGeneFit500):
  * Use a consistent pipeline (scale→PCA→neighbors→UMAP) on combined_downsampled_50k.h5ad with the specified gene set.
  * Color by subclass.
  * Make plots clean: no per-point text labels; no cluttered legends; consistent color palette; dot size ~2–3; alpha ~0.6; high DPI; identical axis limits across panels.
  * Save high-res images as: figures/umap_<PANELNAME>_clean_hires.(png|pdf). Example names: umap_HVG500_clean_hires.png, umap_Final500_clean_hires.png, etc.

3) Do not alter any gene lists or rankings. Only produce metrics and figures as described.

When finished, leave a short markdown note in selection_expert/methods.md describing the added 500-panel benchmarking and the UMAP rendering changes (date-stamped).
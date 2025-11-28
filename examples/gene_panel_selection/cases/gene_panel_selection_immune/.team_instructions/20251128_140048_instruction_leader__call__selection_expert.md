Phase 3b — Complete remaining method (Random Forest) and benchmarking curves/overlaps

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
Prepared AnnData: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000_prepared.h5ad
label_key: leiden

Tasks:
1) Random Forest multiclass classifier to get feature importances across all 3k HVGs; export ranked importance and top lists (100..1000) under results_phase3/gene_panels/rf/.
2) Compute ARI vs panel size curves for HVG, SpaPROS, scGeneFit, DE, RF. Also compute NMI and silhouette index if feasible. Save CSVs under results_phase3/benchmarks/ and figures to figures_phase3/ (one figure per metric and method).
3) Generate a Venn diagram of top-1000 overlaps across the five methods and save as figures_phase3/venn_top1000.png. Also create an overlap table (method pairwise intersections) under tables_phase3/overlap_table.csv.
4) Create UMAP comparisons using panels of sizes 200, 600, 1000 for each method; save per-method UMAPs in figures_phase3/umap_panels/.

Return: paths to the main outputs (RF ranking, benchmark metric CSVs and plots, venn, overlap table, UMAPs dir).
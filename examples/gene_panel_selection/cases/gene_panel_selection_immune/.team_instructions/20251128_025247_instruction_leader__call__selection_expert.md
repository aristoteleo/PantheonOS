Project: Human immune oncology panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Phase 3 — Execute selection methods and benchmarking curves

Inputs:
- AnnData (prepared): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000_prepared.h5ad
- label_key: leiden

Tasks:
1) Run the five selection methods: HVG, SpaPROS, scGeneFit, Differential Expression (wilcoxon, groupby=leiden), Random Forest feature importance.
2) For each method, generate ranked gene lists and save to your workdir (CSVs and plain-text top lists for sizes 100,200,400,600,800,1000).
3) Compute ARI vs panel size curves (100..1000) using a standard pipeline (neighbors+Leiden on features limited to the panel). Also compute NMI and silhouette index if feasible.
4) Generate:
   - Venn diagram of method overlaps for the top-1000 of each method
   - A combined summary table (gene x methods + aggregated score)
   - UMAPs for selected panel sizes (e.g., 200, 600, 1000) comparing structure vs all genes

Please save all results under your workdir in clear subfolders (e.g., results_phase3, figures_phase3, tables_phase3) and return the key output paths.
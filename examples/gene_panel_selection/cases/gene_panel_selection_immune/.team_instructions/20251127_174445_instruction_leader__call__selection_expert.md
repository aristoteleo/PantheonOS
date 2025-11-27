Project Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Sub-agent Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/panel_comparison
Active AnnData (use only this for all analyses): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad
Existing method outputs to use (DO NOT recompute methods): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/methods/
Curated 1000-gene panel: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/curated/final_panel_1000.csv

Goal (high level): Build top-1000 gene panels per method using existing ranking/score files; compute panel-only UMAPs and compare to the baseline UMAP from the full 3k HVGs stored in the AnnData; quantify resemblance; compute clustering agreement metrics (ARI, NMI, Silhouette Index using adata labels); and generate a radar plot summarizing ARI, NMI, and SI across all panels. Save figures and metric tables under your sub-workdir. Prepare a concise LaTeX snippet (.tex) to be inserted into report.tex that introduces the comparison and references your figures and tables.

Detailed instructions and constraints:
1) Panels to construct (top 1000 each):
   - HVG: derive from methods/HVG/hvg_stability_ranked.csv (use the primary ranking column).
   - DE: combine available DE rankings from methods/DE (Immune_broad and Malignant_vs_Other); create a unified ranking by the strongest evidence (e.g., min FDR / best rank across contexts) and take top 1000 genes.
   - SpaPROS: use methods/SpaPROS/gene_panels/spapros/spapros_full_table.csv or spapros_scores.csv to rank and take top 1000.
   - scGeneFit: use methods/scGeneFit/gene_panels/scgenefit/scgenefit_scores.csv to rank and take top 1000.
   - Random Forest: use existing methods/RandomForest/gene_panels/random_forest/rf_top_1000.csv directly.
   - Curated: use selection_expert/curated/final_panel_1000.csv.
   Save each 1000-gene list as CSV under panel_comparison/panels/ with clear names (e.g., hvg_top_1000.csv, de_top_1000.csv, spapros_top_1000.csv, scgenefit_top_1000.csv, rf_top_1000.csv, curated_top_1000.csv). Ensure gene symbols match AnnData var_names.

2) Baseline and UMAP resemblance:
   - Baseline reference: the AnnData contains the baseline embedding computed from the full 3k HVGs (use the existing X_umap in the file). Do not recompute baseline.
   - For each panel, recompute neighbors+UMAP using only that panel’s genes (standard Scanpy defaults; fix random_state for reproducibility), on the same 50k cells.
   - Produce side-by-side visual comparison for each panel vs baseline: save as PNGs under panel_comparison/figures/, with filenames like umap_compare_<panel>.png (left: baseline; right: panel-only), colored by adata.obs['cell_type'].
   - Quantify resemblance to baseline using: (a) kNN neighborhood overlap (e.g., mean Jaccard across cells for k=15 between baseline kNN graph and panel kNN graph), and (b) Procrustes-aligned Pearson correlation between pairwise distances or a simpler trustworthiness/continuity measure vs baseline. Record at least one scalar resemblance metric per panel (choose one primary and report both if computed) and include it in the metrics table.

3) Clustering agreement metrics (use existing labels in AnnData):
   - Use adata.obs['cell_type'] as the ground truth label for agreement metrics.
   - For each panel’s embedding/graph, run Leiden clustering with a fixed resolution (tune a single resolution so the number of clusters is within ~20% of the number of unique cell types; use the same resolution for all panels once chosen). Compute ARI and NMI between Leiden clusters and cell_type.
   - Compute Silhouette Index (SI) using the panel UMAP or PCA space with respect to cell_type labels.
   - Save a CSV summary panel_comparison/metrics/panel_metrics.csv with columns: panel, n_genes, n_cell_types, n_clusters, ARI, NMI, SI, resemblance_metric_primary, resemblance_metric_secondary (optional), resolution_used.
   - Create a radar/spider plot comparing ARI, NMI, and SI across all panels (HVG, DE, SpaPROS, scGeneFit, RF, Curated). Save as panel_comparison/figures/panel_radar_ari_nmi_si.png.

4) Reproducibility and artifacts:
   - Save all intermediate AnnData objects (only the embeddings/graphs and the panel name in .uns) to panel_comparison/adatas/<panel>_embedding.h5ad to allow quick re-plotting without recomputation.
   - Save individual UMAP PNGs for each panel colored by cell_type under panel_comparison/figures/umap_<panel>.png, and also the side-by-side comparison vs baseline as requested above.
   - Provide a compact markdown summary at panel_comparison/README.md describing methods, metrics, and file locations.
   - Prepare a LaTeX snippet file at panel_comparison/panel_comparison_section.tex that: (i) introduces the comparison; (ii) includes the radar plot; (iii) includes a representative multi-panel figure with UMAP comparisons; and (iv) references the metrics table path. Keep it self-contained so we can insert it before \end{document}.

Environment context:
- OS: Ubuntu 22.04.5 LTS; Python 3.10.19; packages available per workdir/system_manager/environment_summary.txt. Use scanpy/umap-learn/sklearn as previously in the project. CPU-only.

Please proceed autonomously and save all outputs under your sub-workdir. When finished, report back the key output paths (metrics CSV, radar plot, comparison figures, and LaTeX snippet).
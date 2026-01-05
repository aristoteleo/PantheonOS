Task: Panel comparison against curated panel using existing method outputs only (no method recomputation)

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/
Your sub-workdir for this task: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/panel_comparison

Environment context:
- OS: Ubuntu 22.04.5; Python 3.10.19; ample CPU/RAM; no active CUDA
- Key pkgs available: anndata 0.11.4; scanpy 1.11.5; numpy 2.2.6; pandas 2.3.3; scikit-learn 1.7.2; umap-learn 0.5.9.post2; squidpy 1.6.5; spapros 0.1.5; scGeneFit installed
- Active AnnData (use this only): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad
  • This contains the 3k-HVG feature space; use obsm['X_umap'] as the reference UMAP if present. If missing, compute the reference UMAP on the full 3k HVGs with fixed random_state.

Inputs to use (existing method outputs):
- HVG ranking: selection_expert/methods/HVG/hvg_stability_ranked_with_symbol.csv (fallback: hvg_stability_ranked.csv)
- DE rankings: selection_expert/methods/DE/DE_Immune_broad_ranked_with_symbol.csv and DE_Malignant_vs_Other_ranked_with_symbol.csv
- SpaPROS: selection_expert/methods/SpaPROS/gene_panels/spapros/spapros_full_table.csv (fallback: spapros_scores.csv)
- scGeneFit: selection_expert/methods/scGeneFit/gene_panels/scgenefit/scgenefit_scores.csv
- Curated reference panel (1000 genes): selection_expert/curated/final_panel_1000.csv

Step 1 — Build 1000-gene method panels (no selection-method recomputation):
- HVG: take the first 1000 genes by the provided ranking order/score. Save to panels/hvg_top_1000.csv
- DE: merge the two DE tables by gene symbol; derive a single rank per gene using the minimum (best) rank across contexts; break ties by average rank. Select top 1000. Save to panels/de_top_1000.csv
- SpaPROS: sort by the primary spapros score descending; take top 1000. Save to panels/spapros_top_1000.csv
- scGeneFit: sort by score descending; take top 1000. Save to panels/scgenefit_top_1000.csv
- Ensure gene symbols are the first column named `gene` and are unique; drop genes missing from the active AnnData var_names, logging counts of dropped genes per panel. Keep a manifest panels/panel_manifest.json summarizing source files and counts (requested=1000, present_in_adata, dropped_missing).

Step 2 — Compute panel-only embeddings and compare to the 3k-HVG reference UMAP:
- For each panel (HVG, DE, SpaPROS, scGeneFit) and the curated panel:
  • Subset the AnnData to the genes present; standard Scanpy pipeline: scale=False; PCA (n_comps=50); neighbors (n_neighbors=15, metric='euclidean'); UMAP (min_dist=0.3, random_state=0). Store the 2D embedding per panel and export scatterplots colored by `cell_type` (fallback labels listed below). Save to umaps/umap_{panel}.png where panel in {hvg,de,spapros,scgenefit,curated}.
  • Reference UMAP: use adata.obsm['X_umap'] if available; otherwise compute once on the full 3k HVGs with the same settings and save as umaps/umap_reference.png.
  • Quantify resemblance between each panel UMAP and the reference UMAP using:
    - Procrustes alignment (scipy.spatial.procrustes): report disparity and a similarity metric Q = 1 - disparity.
    - kNN graph agreement: build 15-NN graphs in the reference (3k HVGs, PCA-based) and in the panel (PCA-based); compute mean Jaccard overlap of neighbor sets per cell. Report mean±sd Jaccard.
  • Save side-by-side comparison figures per panel: figures/umap_compare_{panel}.png with left=panel UMAP, right=reference UMAP.

Step 3 — Clustering agreement metrics using existing labels:
- Labels column: primary 'cell_type' (fallbacks in order: 'Cell_type', 'celltype', 'cell_type_broad', 'Cell_type_broad'). Report which one was used.
- For each panel, compute:
  • Leiden clustering on the panel’s neighbor graph (resolution=1.0, key: 'leiden_{panel}').
  • Adjusted Rand Index (ARI) and Normalized Mutual Information (NMI) comparing Leiden clusters to the chosen label column.
  • Silhouette Index (SI) with respect to the chosen label column computed on the panel’s PCA space (50 PCs), metric='euclidean'.
- Save a CSV metrics table at metrics/panel_metrics.csv with columns:
  panel, n_genes, present_in_adata, dropped_missing, ari, nmi, silhouette, procrustes_Q, procrustes_disparity, knn_jaccard_mean, knn_jaccard_sd, label_col_used

Step 4 — Radar plot summary:
- Create a radar plot over panels {Curated, HVG, DE, SpaPROS, scGeneFit} with axes {ARI, NMI, SI}, scale 0..1. Save to figures/panel_comparison_radar.png (and .pdf if convenient).

Step 5 — LaTeX section snippet for report:
- Generate selection_expert/panel_comparison/panel_comparison_section.tex containing a new LaTeX section titled "Panel Comparison" that briefly describes the procedure and includes:
  • The radar plot: includegraphics[width=0.5\textwidth]{selection_expert/panel_comparison/figures/panel_comparison_radar.png}
  • A compact metrics table (3 columns: Panel, ARI, NMI, SI) using the saved CSV values.
  • A representative grid of UMAP comparisons (e.g., 2×2) for Curated, HVG, DE, and SpaPROS: includegraphics for selection_expert/panel_comparison/figures/umap_compare_{panel}.png
- Keep figure paths relative to the project root as used elsewhere in report.tex.

General requirements:
- Do not recompute any selection method; only derive top-1000 lists from the existing ranking files.
- Use fixed random_state=0 for reproducibility.
- Save all artifacts under your sub-workdir in the appropriate subfolders created.
- Be careful with gene symbol matching to adata.var_names; report any drops.
- Return a short summary of outputs with key paths when done.

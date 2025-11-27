Panel comparison: 1000-gene panels across methods

This sub-workdir contains the construction of top-1000 gene panels per method and the code to compute panel-only embeddings and comparison metrics versus the baseline (3k HVG) embedding provided in the active AnnData.

Contents
- panels/: top-1000 lists per method (var_names matching the active AnnData)
  * hvg_top_1000.csv
  * de_top_1000.csv
  * spapros_top_1000.csv
  * scgenefit_top_1000.csv
  * rf_top_1000.csv
  * curated_top_1000.csv
- panel_comparison.ipynb: end-to-end notebook to recompute neighbors+UMAP for each panel, quantify resemblance to baseline, run Leiden clustering and compute ARI/NMI/SI, and save figures and metrics.
- figures/: UMAPs per panel and side-by-side comparisons vs baseline; radar plot panel_radar_ari_nmi_si.png
- adatas/: minimal AnnData files with panel embeddings and kNN indices
- metrics/panel_metrics.csv: table summarizing metrics across panels

Summary (preview of metrics)

panel,n_genes,ARI,NMI,SI
hvg,1000,0.425910,0.636475,-0.184782
de,713,0.392700,0.685921,-0.067501
spapros,1000,0.354436,0.678459,-0.117783
scgenefit,1000,0.501560,0.697306,-0.089273
rf,1000,0.350517,0.677110,-0.066806
curated,544,0.409397,0.685049,-0.095043

Generated artifacts
- Figures:
  * figures/umap_<panel>.png for all six panels
  * figures/umap_compare_<panel>.png for all six panels
  * figures/panel_radar_ari_nmi_si.png
- Embeddings: adatas/<panel>_embedding.h5ad for all six panels
- Metrics: metrics/panel_metrics.csv (complete)

Notes
- Baseline UMAP is taken from the active AnnData (.obsm['X_umap']).
- Neighbors k=15 and Leiden resolution=0.4 across all panels, as requested.
- Trustworthiness computed with baseline as original space; kNN Jaccard computed in UMAP space vs baseline kNN.
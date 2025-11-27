Audit notes (2025-11-27 00:20)

- Active workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/
- Adata (active, to use for all analyses): selection_expert/adata_downsampled_50k_3kHVG.h5ad (50k cells x 3k genes)
- Curated 1000-gene panel: selection_expert/curated/final_panel_1000.csv
- Per-method outputs present:
  - HVG: selection_expert/methods/HVG/hvg_stability_ranked.csv (+ with_symbol)
  - DE: selection_expert/methods/DE/*_ranked_with_symbol.csv (Immune_broad and Malignant_vs_Other)
  - SpaPROS: selection_expert/methods/SpaPROS/gene_panels/spapros/* (scores and full_table)
  - scGeneFit: selection_expert/methods/scGeneFit/gene_panels/scgenefit/scgenefit_scores.csv
  - RF: selection_expert/methods/RandomForest/gene_panels/random_forest/rf_top_*.csv (not requested for this comparison)
- Report present: report.tex (contains methods, figures, and appendix). No Panel Comparison section yet.
- Created directories for new analysis: selection_expert/panel_comparison/{panels,metrics,figures,umaps}

Plan for this task:
1) Build top-1000 panels for HVG, DE, SpaPROS, scGeneFit from existing ranking outputs (no recomputation of selection methods).
2) For each panel (and include curated as baseline): compute UMAP using only panel genes; compare to reference 3k-HVG UMAP (Procrustes and kNN Jaccard); compute ARI/NMI (Leiden vs cell_type) and Silhouette Index (w.r.t. cell_type). Save per-panel metrics and UMAPs.
3) Generate a radar plot over ARI, NMI, SI for all panels.
4) Update report.tex by appending a new Section "Panel Comparison" with the above figures and key metrics, then compile to PDF using /home/erwinpi/texlive/bin/x86_64-linux/pdflatex report.tex.

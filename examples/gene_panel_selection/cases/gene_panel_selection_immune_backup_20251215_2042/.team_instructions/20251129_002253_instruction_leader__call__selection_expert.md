Project: Human immune oncology gene profiling panel — add UMAP similarity
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Task:
Compute UMAP embeddings and a quantitative UMAP similarity metric for 1000-gene panels vs the full 3k reference on the downsampled_3k dataset. Panels to compare:
- Final curated panel: results/final_panel_1000.csv
- Method panels: top 1000 from SpaPROS, scGeneFit, RandomForest, HVG, DE (already saved under results/gene_panels/* or derivable from their score tables)

Requirements:
- Use results/adata_3k_with_basic.pp.h5ad for speed. Label key: cell_type.
- For each panel, compute UMAP on the same preprocessing pipeline (e.g., scale, PCA n_components=30, neighbors=15, min_dist=0.3) and compare to the reference UMAP built from all 3k genes.
- Save outputs:
  - results/umap_similarity.csv (panel, similarity_score; and any auxiliary measures you compute like Procrustes distance, trustworthiness/continuity, or UMAP alignment metrics)
  - results/figures/umap_similarity.png (bar plot of similarity vs reference)
  - Optional: results/figures/umap_grid_reference_and_panels.png (grid of UMAPs colored by cell_type) if feasible on CPU.
- Keep runtime reasonable on CPU. Reuse cached neighbors/PCs when possible.

When done, add a brief note to your phase 2 report indicating the metric used and where the artifacts are saved.
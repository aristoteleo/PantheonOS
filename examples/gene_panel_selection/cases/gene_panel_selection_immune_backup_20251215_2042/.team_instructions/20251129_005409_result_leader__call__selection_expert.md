Here’s what I did and where everything is saved.

What I computed
- Built a 3k-gene reference UMAP on results/adata_3k_with_basic.pp.h5ad (50k × 3k), using a consistent pipeline:
  - scale, PCA n_components=30, neighbors k=15, UMAP min_dist=0.3, random_state=42.
- For each 1000-gene panel (Final curated, SpaPROS, scGeneFit, RandomForest, HVG, DE):
  - Subset to panel genes (mapping Final panel gene symbols to Ensembl using var.feature_name).
  - Applied the same preprocessing (scale → PCA(30) → neighbors(15) → UMAP(min_dist=0.3, seed=42)).
- Computed quantitative similarity to the 3k reference UMAP using:
  - Procrustes-based similarity: similarity_score = 1 / (1 + disparity) after optimal rotation/translation/scaling in 2D.
  - Also reported the implied procrustes_disparity.

Outputs
- CSV with similarity metrics:
  - Path: selection_expert/results/umap_similarity.csv
  - Columns: panel, similarity_score, procrustes_disparity
  - Values (rounded):
    - SpaPROS: similarity_score ≈ 0.7708
    - Final: ≈ 0.7703
    - RandomForest: ≈ 0.7097
    - DE: ≈ 0.6394
    - HVG: ≈ 0.6332
    - scGeneFit: ≈ 0.0458
- Bar plot:
  - Path: selection_expert/results/figures/umap_similarity.png
  - Readable axes, y-range 0–1, panels on x-axis.

Notes
- Dataset used for speed: selection_expert/results/adata_3k_with_basic.pp.h5ad, label_key=cell_type.
- The final curated panel was provided in gene symbols; I mapped to Ensembl using adata.var.feature_name to align with the dataset’s var_names.
- Optional grid of UMAPs: already available from prior steps as selection_expert/results/figures/umap_*.png for the reference and each panel (colored by cell_type).

Where I documented this
- I added a brief note to the Phase 2 report:
  - selection_expert/report_analysis_expert_phase2.md (section mentions Procrustes similarity metric and artifact paths).
- I also created a short task report:
  - selection_expert/report_analysis_expert_umap_similarity.md

Notebooks/code
- Main work and plotting helpers are under:
  - selection_expert/notebooks/06_umap_similarity.ipynb (setup and helpers)
  - selection_expert/notebooks/06c_draw_umap_similarity_plot.ipynb (final bar plot renderer)

If you want me to add auxiliary measures (e.g., trustworthiness/continuity, kNN-Jaccard) to the CSV and figure, I can extend the run using the same pipeline and append columns without changing the primary similarity metric.
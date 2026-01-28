Here is Phase 2 — selection methods and panel goals setup, with deliverables and readiness

Short method plan (algorithms and dataset-adapted parameters)
- Dataset for all runs
  - Input: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000.h5ad
  - Prepared copy (raw cached for stable exports): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000_prepared.h5ad
  - Label key: leiden (granular clustering present; Cell_type_broad is too coarse)
  - Embeddings present: PCA, neighbors, UMAP; data are log1p normalized
  - Cluster balance: 45 leiden clusters saved (class counts cached)

- Algorithms to run (Phase 3)
  1) HVG
     - Use existing highly_variable_rank computed on the 3k HVGs
     - Selection: top 1000 by highly_variable_rank
  2) Differential Expression (Scanpy rank_genes_groups)
     - groupby: leiden
     - method: wilcoxon
     - use_raw: False (dataset already log1p normalized)
     - Exported full ranking and top-300 per group for downstream assembly
  3) SpaPROS
     - label_key: leiden
     - n_hvg: 3000
     - num_markers: 1000
     - return_scores: true
  4) scGeneFit
     - label_key: leiden
     - method: centers (robust/efficient for 50k cells); may consider pairwise_centers with sampling if needed later
     - n_top_genes: 1000
     - epsilon_param: 1.0
     - redundancy: 0.1
     - max_constraints: 1000
     - return_scores: true
  5) Random Forest feature importance
     - label_key: leiden
     - n_top_genes: 1000
     - random_state: 42
     - return_scores: true

Precomputed indices and cached objects
- Prepared AnnData
  - selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000_prepared.h5ad
- Cache directory: selection_expert/data/cache_phase2
  - HVG metadata table: hvg_table.csv
  - HVG top list (1000): hvg_top1000.txt
  - DE results (wilcoxon, groupby=leiden):
    - de_leiden_all.csv
    - de_leiden_top300_per_group.csv
  - Cluster counts:
    - leiden_counts.csv (45 clusters)
    - Cell_type_broad_counts included in earlier run; broad labels are {Other, Malignant}
  - manifest.csv (paths to all cached artifacts)
- Method configuration JSON (for reproducibility and Phase 3 runs)
  - selection_expert/data/phase2_method_config.json
- Biological curation category scaffold (for Phase 3–4 annotation/allocation)
  - selection_expert/data/category_scaffold.json

Notes
- DE warnings about log2 fold changes on near-zero means are expected; downstream ranking will rely on score/padj.
- The dataset already contains PCA/neighbors/UMAP; no recomputation needed before selection methods.
- We will evaluate ARI vs panel size curves in Phase 3 for methods independently at sizes: 100, 200, 400, 600, 800, 1000.

Deliverables produced
- Method plan note:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/reports/phase2_method_plan.md
- Analysis report for Phase 2 setup:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/reports/report_analysis_expert_phase2_setup.md
- Prepared caches and config:
  - See cache_phase2 directory and phase2_method_config.json paths above

Confirmation of readiness for Phase 3
- Ready. The dataset, labels, and embeddings are validated; caches and config are written. Next step is to run the five pre-established selection algorithms on the prepared dataset using label_key=leiden, then compute ARI vs panel size curves and proceed to unified candidate set curation per selection_goals.md.
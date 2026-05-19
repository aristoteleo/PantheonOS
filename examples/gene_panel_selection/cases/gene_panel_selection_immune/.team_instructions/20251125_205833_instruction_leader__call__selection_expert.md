Context:
- Project: 1000-gene Immune-Oncology gene panel for human TME profiling intended for spatial (Vizgen-style) deployment.
- Dataset path: /home/erwinpi/data/adata.h5ad (bioRxiv 2024 preprint DOI: 10.1101/2024.01.17.576110).
- Workdir (project): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Environment: See /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md (scanpy 1.11.5, anndata 0.11.4, scvi-tools 1.3.3, spapros 0.1.5, scGeneFit installed, sklearn 1.7.2). 2x H100 GPUs available; ~51 GB free disk.

Goal:
Execute the panel selection strategy and curation to produce a final, fully annotated 1000-gene IO panel optimized for separability, immune profiling depth, cancer pathway resolution, interpretability, and spatial (Vizgen-style) deployment.

High-level tasks
1) Finalize the QC baseline for panel selection
- Produce a symbol-indexed AnnData (reindex by var['feature_name'] with duplicate-safe disambiguation), saved as adata_qc_initial.h5ad.
- Export initial labels from obs (prefer Cell_type_broad if present; otherwise author_cell_type). Save cell_labels_initial.tsv.
- Save marker panels:
  - UMAP colored by initial labels
  - Dotplot of canonical markers across initial labels and Leiden clusters

2) Run complementary selection methods to generate candidate genes
- HVG: batch-aware HVG selection.
- Differential Expression: per cell type vs rest (One-vs-Rest) and malignant vs non-malignant (if inferable from epithelial/tumor markers). Use conservative multiple-testing control; retain robust markers.
- scGeneFit: select discriminative markers for major cell types.
- SpaPROS: optimize for separability with spatial/FISH constraints in mind.
- Random Forest: feature importance for classifying major cell types and malignant state.

3) Aggregate and score
- Combine outputs into a unified candidate table with per-gene metrics from each method, normalized scores, and a composite rank.
- Include expression robustness metrics (mean, detection rate), batch stability, and potential probe-design cautions (high homology families, pseudogenes).

4) Biological constraints and coverage
Ensure the candidate and final panel comprehensively cover:
- Immune lineages: T, NK, B, Plasma; Myeloid (macrophage, monocyte, dendritic cell, neutrophil); plus stromal and endothelial; malignant epithelial.
- Checkpoints/exhaustion (PDCD1, CTLA4, LAG3, HAVCR2, TIGIT, PDCD1LG2, PD-L1/CD274), activation and cytotoxicity (GZMB, PRF1, NKG7, IFNG), cytokines/chemokines (IL/TNF/IFN families; CCL/CXCL axes), inflammation modules.
- Cancer pathways: MAPK, PI3K/AKT/mTOR, JAK-STAT, TGF-β, WNT/β-catenin, NF-κB, Hippo, Notch; oncogenes and tumor suppressors.
- Cell states: cell cycle/proliferation, DNA damage/replication stress, hypoxia, angiogenesis, EMT, senescence, oxidative/ER stress.
- Malignant vs non-malignant distinction and intratumoral heterogeneity markers as feasible.
- Spatial/FISH suitability heuristics: prefer unique sequences, robust expression windows, avoid extensive paralogy/pseudogenes when problematic.

5) Curate to the final 1000 genes
- Deduplicate and balance categories with explicit target counts per category (immune lineages, cytokines/chemokines/checkpoints, pathways/oncogenes/TSGs, cell-state programs, malignant markers, stromal/endothelial, housekeeping/normalization controls).
- Produce the final 1000-gene panel with full annotations: gene symbol, category, subcategory, brief function/pathway, expected expression pattern/cell types, and (if feasible) key literature references.
- Provide export formats suitable for downstream use:
  - panel_1000.tsv (tabular)
  - panel_1000.json (schema with category metadata)
  - panel_1000_vizgen_targets.txt (one gene per line), considering probe naming constraints.

6) Validation snapshots
- Train a lightweight classifier (e.g., logistic regression or RF) using only the 1000 genes to quantify cell-type separability; report macro-F1/accuracy via cross-validation.
- UMAP/neighbor graph using only the 1000 genes; visualize separability.
- Summaries of coverage per category and per cell type.

Deliverables (save all to your workdir)
- Symbol-indexed AnnData: adata_qc_initial.h5ad
- Labels: cell_labels_initial.tsv
- Selection outputs per method and the aggregated candidate table with scores: candidates_aggregated.tsv (+ per-method files)
- Final curated panel files: panel_1000.tsv, panel_1000.json, panel_1000_vizgen_targets.txt
- Figures: marker dotplots, UMAPs, coverage barplots, classifier performance; save into figures/
- A brief methods-and-results summary: selection_summary.md describing what you did, key choices, and headline results

Keep runtime and disk in check; if needed, operate on the downsampled working set for algorithm prototyping, but base final scoring on full data when feasible. Reuse provided annotations. Avoid overfitting; prefer robust, interpretable genes.

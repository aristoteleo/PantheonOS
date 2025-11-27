Project: PBMC3k gene panel selection

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert

Input adata path to use: please read from used_adata_path.txt in your workdir (currently: /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad). Note: this object appears to contain ~1838 genes (likely an HVG subset). Please proceed with selection from the available genes while noting this limitation in your outputs.

Environment: Python 3.10, scanpy 1.11.5, anndata 0.11.4, sklearn, umap-learn; 56 CPUs, ample RAM, no GPU.

Task: Multi-method gene panel selection and integration for a final curated panel of 500 genes.

Biological context and goal:
- Human PBMC (PBMC3k). We want a robust 500-gene panel that distinguishes major PBMC cell types and key subtypes (CD4/CD8 T, NK, B, CD14+ and FCGR3A+ monocytes, dendritic cells, megakaryocytes), preserves important functional programs (cytotoxicity, interferon response, TCR signaling, antigen presentation), and avoids over-representation of mitochondrial/ribosomal/housekeeping genes unless clearly informative.

Please perform the following at a high level (you know the detailed steps):
1) Compute candidate rankings by multiple methods:
   - Highly variable genes (HVG) within this object.
   - SpaPROS.
   - scGeneFit.
   - Differential expression markers by the provided louvain/cell-type labels (one-vs-rest; use appropriate multiple testing control; rank by a composite of logFC and specificity).
   - Random Forest (multiclass) feature importances using the louvain/cell-type labels; include cross-validation.
   Save per-method ranked lists, with scores and diagnostics.

2) Integrate results into a consensus ranking:
   - Combine per-method ranks/scores, emphasize genes consistently selected across methods and covering all cell types.
   - Penalize excessive MT/RP genes unless critical; enforce diversity across cell types and pathways; limit correlated redundancy.
   - Curate to a final list of exactly 500 genes.

3) Evaluate the panel:
   - Recompute neighborhood graph/UMAP using only panel genes; re-cluster; compare ARI/NMI vs. labels.
   - Train a classifier using only panel genes; report accuracy/macro-F1 vs. full feature baseline if feasible.
   - Provide coverage plots showing per-cell-type marker representation.

4) Outputs to save in your workdir:
   - final_panel_500.csv (with columns: gene, consensus_rank, methods_support, cell_type_annotations if inferred, notes, qc flags)
   - final_panel_500.txt (one gene per line)
   - Per-method rankings (CSV/TSV)
   - Evaluation metrics (JSON/TXT) and figures (UMAP with labels using panel; confusion matrix; barplots of marker coverage)
   - A short README.md summarizing the approach and findings, including limitations due to the preprocessed HVG-limited input.

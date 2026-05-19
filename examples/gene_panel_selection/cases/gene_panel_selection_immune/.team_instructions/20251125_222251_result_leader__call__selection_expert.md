Task 1c completed. I audited the dataset, performed QC, strategically downsampled to <50k while preserving rare TME cell types, computed a preliminary embedding/clustering, and produced coarse annotations. All outputs are under your sub-workdir.

Key results
- Input: /home/erwinpi/data/adata.h5ad
- Original size: 355,941 cells × 22,781 genes
- Species: Homo sapiens (uns['organism'])
- Major obs fields: donor_id, Study_name, Study_name_cancer, Cell_type_broad (Malignant/Other), author_cell_type/_update, nCount_RNA, nFeature_RNA, tissue, disease, sex, assay
- Existing obsm: X_umap present in source
- QC metrics computed:
  - total_counts, n_genes_by_counts, pct_counts_mt (MT-), pct_counts_ribo (RPL/RPS)
  - Doublet scoring: scrublet not installed; added placeholder doublet_score_est (NaN)

Downsampling (IMPORTANT)
- Strategy: Stratified by Study_name_cancer and a coarse_type derived from author_cell_type_update to preserve rare TME classes (Treg, DC, Plasma, Mast, etc.).
- Mapping to coarse_type included: Malignant, T, Treg, NK, B, Plasma, MDSC, Dendritic, Neutrophil, Mono/Macro, Endothelial, Fibroblast, Epithelial, Mast, Lymphoid, Other (fallback uses Cell_type_broad if needed).
- Cap: up to 600 cells per (Study_name_cancer, coarse_type).
- Selected: 29,535 cells (<=50k; no further reduction needed).
- Saved downsampled AnnData:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled.h5ad
  - Path recorded in downsampled_adata_path.txt (same directory)

Preliminary preprocessing and annotation (on downsampled set)
- Soft filter: n_genes_by_counts ≥ 200, pct_counts_mt ≤ 20 (kept 29,482 of 29,535)
- Normalization/log1p; HVGs (Seurat v3, 4,000; batch_key=Study_name_cancer), PCA (50), neighbors (PCs=40), UMAP, Leiden (resolution=0.8; 33 clusters)
- Marker UMAPs: robust case-insensitive matching to var_names (feature_name)
- Coarse compartments assigned using canonical markers:
  - Immune: PTPRC+
  - Epithelial/Malignant: EPCAM/KRTs+
  - Endothelial: PECAM1/VWF+
  - Fibroblast: COL1A1/COL1A2/PDGFRA+
- Saved a preprocessed downsampled AnnData:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_preprocessed.h5ad

Deliverables and file locations
- Notebook (full reproducibility):
  - selection_expert/task1c_dataset_audit.ipynb
- QC
  - QC metrics table: selection_expert/qc/qc_metrics.tsv.gz
  - Plots:
    - selection_expert/figures/qc_histograms.png
    - selection_expert/figures/qc_scatter_counts_genes_mito.png
    - selection_expert/figures/qc_violin_counts_genes_mito.png
- Downsampling
  - Downsampled AnnData: selection_expert/adata_downsampled.h5ad
  - Downsampled path record: selection_expert/downsampled_adata_path.txt
- Preliminary analysis figures
  - selection_expert/figures/umap_coarse_leiden.png
  - selection_expert/figures/umap_marker_panels.png
  - selection_expert/figures/umap_compartment.png
- Summaries
  - selection_expert/dataset_summary.txt
  - selection_expert/dataset_summary.json
- Analysis report (this step):
  - selection_expert/report_analysis_expert_task1c_dataset_audit.md

Notes
- Doublet scoring: scrublet not present; can be installed later if you want doublet estimates.
- Disk footprint: downsampled ~606 MB; preprocessed ~167 MB.
- Gene symbol handling: dataset’s var included feature_name; ensured case-insensitive matching for markers and resolved var index/column name conflict before saving.

From now on, we will use the downsampled dataset for all steps:
- Downsampled AnnData path (as requested, in text file):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/downsampled_adata_path.txt
  - It contains: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled.h5ad
- Preprocessed version (for embeddings and quick visualizations):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_preprocessed.h5ad

Next step preview
- Proceed to gene panel selection methods (HVGs, DE-based, scGeneFit, SpaPROS, RandomForest) on the downsampled dataset; then curate a 1000-gene immune-oncology panel aligned with TME goals (immune populations, cancer signaling, cytokine/chemokine states, malignant vs non-malignant, and cell-state axes).
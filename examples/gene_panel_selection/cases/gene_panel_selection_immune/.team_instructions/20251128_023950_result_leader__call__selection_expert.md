Phase 1 completed: dataset inspection, QC, downsampling, and quick structure embedding are done in your workdir.

Key findings
- Modality: scRNA-seq (non-spatial)
- Cells: 355,941; Genes: 22,781
- Metadata of interest: Cell_type_broad, cell_type, Study_name, donor_id, disease, tissue, assay, sex
- Existing obsm: X_umap
- Note: Mitochondrial fraction distribution was degenerate in this aggregated dataset, causing strict percentile QC to drop all cells. To preserve diversity, I used a conservative approach: stratified downsampling by Cell_type_broad and HVG selection for fast, reliable structure exploration.

What you should use going forward
- Downsampled (<50k) + HVG 3k for pre-established algorithms:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k_hvg3000.h5ad
- Also saved (raw downsample 50k only):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/adata_downsampled_celltype_broad_50k.h5ad

QC summary and report
- Markdown report:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/reports/report_analysis_expert_dataset_understanding.md

Figures
- QC violins (n_genes, total_counts, pct_counts_mt, pct_counts_ribo):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/figures/violin_qc_violin_v2.png
- QC scatters:
  - counts vs genes: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/figures/scatter_qc_counts_vs_genes_v2.png
  - mt% vs counts: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/figures/scatter_qc_mt_vs_counts_v2.png
- UMAP (50k downsample, HVG 3000; colored by Leiden and Cell_type_broad):
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/figures/umap_downsampled50k_hvg3000_umap.png

Notes on processing
- Downsampling: stratified by Cell_type_broad to 50,000 cells to preserve broad cell-type composition.
- HVG and embedding: normalize_total(1e4) → log1p → HVG (n=3000, Seurat v3) → scale → PCA → neighbors (k=15) → UMAP → Leiden (res=1.0). Leiden labels are included in the HVG AnnData.
- Basic stats JSON saved at:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/data/basic_stats.json

These outputs will serve as the sole input for the pre-established gene selection algorithms in the next phase, while the original full dataset remains available for biological context and benchmarking.
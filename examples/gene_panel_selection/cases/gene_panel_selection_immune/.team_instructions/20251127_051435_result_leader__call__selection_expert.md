Plan and actions taken
- Set up a dedicated notebook and outputs in your workdir:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/01_qc_downsample.ipynb
- Loaded the dataset and inspected structure/metadata.
- Performed basic QC metric computation and saved plots.
- Confirmed malignant annotations and useful metadata fields.
- Downsampled to 50,000 cells using stratified sampling by [tissue, cell_type] to preserve composition.
- Reduced genes to 3,000 highly variable genes (HVG) to meet <=3k genes guideline.
- Saved the downsampled adata and set the active adata path for all subsequent steps.

Key dataset info
- Source h5ad: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- AnnData: 355,941 cells × 22,781 genes
- obsm: precomputed X_umap present
- obs useful columns:
  - cell_type (contains “malignant cell” plus rich immune labels)
  - Cell_type_broad (Malignant vs Other)
  - tissue (14 tissues; breast dominant)
  - donor_id, Study_name_cancer and other study/sample descriptors

QC findings
- total_counts per cell: median ~2177 (IQR ~1649–2616)
- n_genes_by_counts: median ~1372 (wide range)
- pct_counts_mt: median ~1.85%, 75th percentile ~2.45%
- No additional filtering applied; metrics look healthy across this integrated set.

Malignancy annotations
- Present and usable:
  - cell_type includes “malignant cell”
  - Cell_type_broad = Malignant vs Other

Downsampling
- Threshold exceeded (>50k cells), so downsampling applied.
- Strategy: stratified by [tissue, cell_type]
  - Minimum per stratum 20, then proportional allocation to reach 50,000
- Result: 50,000 cells × 22,781 genes saved as:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k.h5ad
- UMAP sanity plots on downsample saved.

Gene subsetting (<=3k genes)
- Selected 3,000 HVGs (Seurat v3 flavor after normalize_total + log1p) from the 50k subset.
- Saved as:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

Active adata for downstream steps
- Set as THE ONLY input going forward:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad
- Recorded in:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/active_adata_path.txt

QC figures saved
- Directory: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/qc_figures
- Files:
  - qc_histograms.png
  - qc_violin.png
  - umap_cell_type.png
  - umap_Cell_type_broad.png
  - umap_downsample_cell_type.png
  - umap_downsample_Cell_type_broad.png
  - umap_downsample_tissue.png

Compositions saved
- composition_before.tsv
- composition_after.tsv

QC summary note
- A full markdown report with key observations and rationale is saved at:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/report_analysis_expert_qc_downsample.md

Brief metadata utility notes for panel design
- Malignant vs non-malignant labels available for separation and tumor-state coverage.
- Immune lineages and states richly annotated (T, NK, B, plasma, myeloid, DCs, Tregs, Th17, etc.) to inform lineage/state marker selection.
- Tissue diversity supports generalizable TME markers for Vizgen-style panels.

Downsampled adata path (for immediate use)
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad

Note on figure readability
- Histograms render clearly; initial UMAP legends are dense due to many categories. If desired, I can regenerate UMAPs with larger figure size and optimized legends (e.g., external legend, increased font size) in a quick follow-up.
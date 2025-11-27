QC and Downsampling Report — Immune-oncology 1000-gene panel (Human TME)

Dataset
- Input h5ad: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad (bioRxiv 2024 preprint DOI: 10.1101/2024.01.17.576110)
- AnnData summary: 355,941 cells × 22,781 genes
- Existing embeddings/metadata:
  - obsm: X_umap present (pre-computed)
  - obs: rich annotations including cell_type, Cell_type_broad (Malignant vs Other), tissue, donor_id, study labels, etc.
  - var: gene symbols in `feature_name`, additional QC/feature fields

QC summary
- Total counts per cell (median ~2,177; IQR ~1,649–2,616)
- Detected genes per cell (median ~1,372; wide range due to cross-study integration)
- Mitochondrial percentage (median ~1.85%, low overall; 75th percentile ~2.45%)
- No aggressive cell filtering applied at this stage, as the dataset is already integrated; metrics look reasonable.
- Figures saved to qc_figures/:
  - qc_histograms.png (total_counts, n_genes_by_counts, pct_counts_mt)
  - qc_violin.png
  - umap_cell_type.png, umap_Cell_type_broad.png (original embedding)

Composition and annotations
- Malignancy: explicit annotations present
  - cell_type includes label "malignant cell"
  - Cell_type_broad has two levels: Malignant vs Other
- Tissue diversity: 14 tissues represented (dominant: breast; others include kidney, multiple skin sites, brain, liver, etc.)
- Immune lineages well represented (T, B, myeloid, plasma, endothelial, fibroblasts, etc.)
- Top categories (cell_type): malignant cell (90,270), T cell (80,010), fibroblast (37,302), myeloid cell (30,027), B cell (19,107), with many refined T-cell states (CD8mem, CD8ex, Tregs, etc.).

Downsampling rationale and procedure
- Requirement: Operate on ≤50k cells and ≤3k genes for panel selection.
- Strategy: Stratified downsampling to preserve composition across tissues and cell types.
  - Strata used: [tissue, cell_type]
  - Minimum per stratum: 20 cells (capped by availability), then proportional allocation to reach target N=50,000.
- Resulting subset: 50,000 cells × 22,781 genes (AnnData object adata_downsampled_50k.h5ad)
- UMAP sanity plots of downsampled subset saved: umap_downsample_cell_type.png, umap_downsample_Cell_type_broad.png, umap_downsample_tissue.png

Gene subsetting to ≤3,000 genes
- Method: Highly variable genes (Seurat v3 flavor) on the downsampled set after normalize_total + log1p.
- Selected 3,000 HVGs prioritized for robust signal across batches and cell states.
- Saved dataset: 50,000 cells × 3,000 genes (adata_downsampled_50k_3kHVG.h5ad)

Active dataset for downstream steps
- As per the project workflow, the active dataset has been set to the HVG-restricted downsample:
  - /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert/adata_downsampled_50k_3kHVG.h5ad
- This path is recorded in active_adata_path.txt in the selection_expert workdir, and will be the ONLY input for subsequent panel selection.

Notes relevant for panel selection
- Malignant vs non-malignant labels are available and will support tumor vs microenvironment discrimination, subclone identification, and malignant-specific signaling coverage.
- Rich immune annotations (T/NK/B/plasma, myeloid, DCs, Tregs, Th17, etc.) will facilitate lineage- and state-resolving marker discovery.
- Tissues represented ensure the panel generalizes across TME contexts.

Files produced
- selection_expert/qc_figures/
  - qc_histograms.png, qc_violin.png, umap_cell_type.png, umap_Cell_type_broad.png, umap_downsample_cell_type.png, umap_downsample_Cell_type_broad.png, umap_downsample_tissue.png
- selection_expert/adata_downsampled_50k.h5ad (50k × 22,781)
- selection_expert/adata_downsampled_50k_3kHVG.h5ad (50k × 3,000)
- selection_expert/active_adata_path.txt
- selection_expert/composition_before.tsv, selection_expert/composition_after.tsv

Next steps
- Proceed with marker selection algorithms (HVG scores, DE by cell type/state, scGeneFit, SpaPROS, Random Forest) on the active dataset.
- Integrate biological knowledge (GeneCards, GO, UniProt) for curated immune-oncology 1000-gene panel optimized for spatial (Vizgen) deployment.
Project: Immune-oncology 1000-gene panel (human TME)

Workdirs:
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Environment context:
- See environment summary at: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md
- Python env: scanpy/anndata stack installed; scGeneFit and SpaPROS available; scikit-learn; scvi-tools optional; GPU available.

Dataset:
- adata_path: /home/erwinpi/data/adata.h5ad
- Source: bioRxiv 2024 preprint DOI: 10.1101/2024.01.17.576110

Task step 1 (Understanding the dataset):
- Load the AnnData and perform basic EDA and QC to understand structure and quality.
- Summarize: n_cells, n_genes, batch fields, tumor type(s), key obs columns, sparsity, top expressed genes.
- Compute QC metrics (mito/ribo), filter low-quality cells/genes as appropriate; document thresholds.
- Normalize, log1p, find HVGs; compute PCA/Neighbors/UMAP; Leiden clustering.
- Provide initial cell-type annotation at major level (T, NK, B, Plasma, Myeloid: Mono/Macro/DC/Neutro, Fibro, Endothelial, Epithelial), using canonical markers and any metadata present.
- Infer malignant vs non-malignant labels using expression heuristics and/or CNV if feasible.
- Score cell cycle; produce figures (QC plots, UMAPs, marker dotplots/heatmaps) and write an annotated adata file for downstream steps.

Deliverables:
- Save an annotated AnnData: selection_expert/adata_qc_annotated.h5ad
- A markdown summary with key stats, decisions, and figure references: selection_expert/dataset_qc_annotation_summary.md
- Figures under selection_expert/figures/
- Log all random seeds and parameters.

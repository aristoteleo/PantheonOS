Project: 1000-gene immune-oncology panel for human TME
Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Inputs
- adata_path: /home/erwinpi/data/adata.h5ad
- Dataset source: bioRxiv preprint 2024 (DOI: 10.1101/2024.01.17.576110)

Environment context
- See consolidated environment details in /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md
- GPU: 2x H100 available (CUDA 12.8 via torch). JAX CPU-only. Disk space somewhat tight (~51G free on /); keep intermediates light and reuse outputs.

Task 1c: Please audit and understand the dataset
- Load the adata and perform a quick audit: n_cells, n_genes, species (should be human), tissue(s), batch fields, existing annotations (obs columns), and technical covariates.
- Perform standard scRNA-seq QC (cells and genes): mitochondrial content, total counts, n_genes_by_counts, ribosomal fraction, doublet score if feasible. Save QC metrics and plots.
- If n_cells > 50,000, downsample strategically for panel selection while preserving rare but important TME cell types (e.g., DC subsets, Tregs, MDSCs, neutrophils). Use stratified sampling by coarse cell types/batches if annotations exist; otherwise compute a coarse clustering on HVGs first then stratify by clusters and batches.
- IMPORTANT: If you downsample, save the new anndata path and communicate it back in a text file in your workdir; from now on we will consider only this downsampled anndata for all steps.
- Normalize/log1p and compute a preliminary embedding and clustering; annotate major compartments if possible (immune vs malignant vs stromal) using canonical markers.
- Save a brief dataset summary (text) and key figures to your workdir.

Biological context and overall goal
- We aim to design a 1000-gene panel for human tumor microenvironment profiling (Vizgen-style compatibility). It must:
  1) Resolve major immune populations (T, NK, B, plasma; macrophages/monocytes/DCs/neutrophils; Tregs, MDSCs),
  2) Characterize cancer signaling (oncogenes, TSGs, cell cycle, DNA damage/stress, hypoxia/angiogenesis/EMT/proliferation),
  3) Capture cytokine/chemokine states incl. exhaustion markers,
  4) Distinguish malignant vs non-malignant and support subclone detection,
  5) Enable cell-state analyses (exhaustion/activation/proliferation/senescence/stress).

Deliverables for this step
- QC report and plots
- Note whether downsampling was performed and, if so, provide the path to the downsampled adata (save it and note the path in a text file: downsampled_adata_path.txt)
- Preliminary coarse annotation and summary text file
- Keep all outputs under your sub-workdir.
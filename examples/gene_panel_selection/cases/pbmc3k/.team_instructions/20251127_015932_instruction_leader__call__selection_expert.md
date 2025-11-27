Project: PBMC3k gene panel selection

Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
Workdir for the sub-agent: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert

Input adata path: /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad

Environment notes:
- Python 3.10, scanpy 1.11.5, anndata 0.11.4, sklearn, umap, etc. 56 CPUs, ~1.6 TB RAM, no GPU.

Task step 1 (Understanding the dataset):
- Load the adata, perform a quick dataset understanding and QC summary (n_cells, n_genes, basic QC metrics; check for cell count >50k and perform downsampling to <50k cells if needed). For PBMC3k likely no downsampling needed.
- If downsampling is performed, save the downsampled adata and report the new adata path; otherwise, save a validated copy/reference path to be used for all subsequent steps. IMPORTANT: Save the path you want us to use next in a text file e.g., used_adata_path.txt in your workdir.
- Create a brief EDA/QC notebook or HTML/PNG outputs with basic plots (UMAP/TSNE if available, QC distributions) saved in your workdir.

Biological context and goal:
- Human PBMC dataset (PBMC3k). Goal is to curate a 500-gene panel that robustly discriminates major PBMC cell types (T cell subsets, B cells, NK cells, monocytes, dendritic cells) and preserves key functional signals (e.g., cytotoxicity, interferon response), while avoiding over-representation of housekeeping and mitochondrial/ribosomal genes unless clearly informative.

Deliverables for this step:
- QC/EDA outputs
- used_adata_path.txt with the annData file path to use downstream
- A short summary text file with key stats and observations
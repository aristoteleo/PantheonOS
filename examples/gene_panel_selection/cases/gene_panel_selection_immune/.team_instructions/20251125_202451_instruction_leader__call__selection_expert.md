Context:
- Project: 1000-gene Immune-Oncology gene panel for human TME profiling intended for spatial (Vizgen-style) deployment.
- Dataset path: /home/erwinpi/data/adata.h5ad (bioRxiv 2024 preprint DOI: 10.1101/2024.01.17.576110).
- Workdir (project): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert
- Environment: See /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md. scverse stack available (scanpy 1.11.5, anndata 0.11.4, scvi-tools 1.3.3, spapros 0.1.5, scGeneFit installed, sklearn 1.7.2). 2x H100 GPUs available; ~51 GB free disk.

Goal:
Perform the Understanding & QC step (Workflow step 1c) to understand the dataset and prepare for panel selection. Specifically:
- Load the AnnData; audit obs/var; verify species (human) and TME context.
- Basic QC (cells/genes filtering, MT/ribo; doublets if feasible), normalization, HVG exploration.
- Assess batch/condition structure; build embeddings and clustering; preliminary cell-type annotation for major TME compartments (T, NK, B, Plasma, Myeloid: macrophage/monocyte/DC/neutrophil; stromal; malignant vs non-malignant if feasible). Use canonical markers and, if available, provided annotations.
- Summarize key dataset characteristics (n_cells, n_genes, batches, tissues, tumor types), and save diagnostic figures (QC metrics, UMAP/labels, marker expression).

Deliverables:
- A concise dataset summary (dataset_summary.md) saved to your workdir.
- Saved figures (PNG/PDF) for QC, UMAP, marker panels.
- If annotations exist, export a basic cell-type label column; if not, provide initial labels.

Please keep outputs small enough to fit within disk constraints. Reuse existing annotations if present. Avoid long-running models; prioritize practical, robust steps.

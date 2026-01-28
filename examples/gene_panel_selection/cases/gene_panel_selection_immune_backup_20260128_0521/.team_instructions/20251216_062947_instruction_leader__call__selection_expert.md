Project: Human immune oncology 1000-gene profiling panel

Workdir
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/selection_expert

Dataset
- adata_path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Source: bioRxiv Preprint 2024.01.17.576110 (human tumor microenvironment)

Environment context
- See environment.md at /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/environment.md
- System has high RAM/CPU, GPUs present but numba CUDA not configured; proceed with CPU-based workflows using pynndescent and memory-aware settings.

Goal
- Perform dataset inspection and QC; assess structure, cell types if annotated, batch info, etc.
- If dataset > 500k cells: downsample; here n_cells~356k, so full dataset is acceptable. If you judge computational tradeoffs favor subsampling for algorithmic comparability, you may generate a stratified downsample and save its path; if any downsample is used, save its adata path and use it for pre-established selection algorithms. Gene subsetting only if >30000 genes (not the case).
- Prepare for panel selection with pre-established algorithms: HVG, SpaPROS, scGeneFit, Differential Expression, Random Forest. You will later merge/curate subpanel for separability and complete to final 1000 genes using biological context.

Deliverables
- Save a dataset inspection/QC report (markdown) with key stats, QC thresholds, potential filtering, and whether downsampling was applied. Include the path to the adata that will be used for algorithm runs (original or downsampled). Save any figures in your agent workdir.
- Name report file: dataset_QC_overview.md
- If downsampling performed, save new .h5ad in your agent workdir and record its path in dataset_QC_overview.md
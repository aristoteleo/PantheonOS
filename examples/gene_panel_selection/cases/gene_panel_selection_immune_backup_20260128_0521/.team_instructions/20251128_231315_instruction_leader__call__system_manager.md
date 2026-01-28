Project: Human immune oncology gene profiling panel
Workdir (project root): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task:
1) Audit the computational environment (hardware + OS + Python + key libs) and write a concise environment.md at the project root. Include CPU/GPU availability, RAM/disk, Python version, and installed scverse stack versions if present (anndata, scanpy, scikit-learn, numpy, pandas, matplotlib, seaborn, umap-learn, scvi-tools if available).
2) Ensure required packages for scRNA gene panel selection and plotting are installed/usable for the downstream selection_expert (scanpy/scikit-learn/umap-learn, statsmodels, seaborn, matplotlib, scipy, numpy, pandas). Do minimal installation only if missing.
3) Save any logs under your agent workdir.

Notes:
- Do not alter any of the data files.
- Keep the report minimal and place environment.md at the project root.
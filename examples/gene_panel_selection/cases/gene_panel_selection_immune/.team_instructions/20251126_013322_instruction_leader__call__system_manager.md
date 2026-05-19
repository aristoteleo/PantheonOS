Task: Verify/record computational environment for gene panel selection; ensure required packages are available.

Context
- Project: 1000-gene immune-oncology panel for human TME profiling
- Workdir for the project: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
- Workdir for you: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager
- Input dataset (AnnData): /home/erwinpi/data/adata.h5ad

Instructions
1) Audit system and Python env (hardware, CUDA, python, key packages) and append to/refresh the root environment.md only if something changed materially since the last audit on 2025-11-25. Otherwise, just log details to your workdir.
2) Confirm availability of: scanpy>=1.9, anndata>=0.9, scvi-tools, squidpy, scGeneFit, spapros, scikit-learn, numpy, pandas, scipy, matplotlib, leidenalg, igraph, pyarrow, statsmodels. If missing or incompatible, install/upgrade in the gps env.
3) Record: frozen requirements, pip list, system info, python env info to your workdir.
4) Note disk free space and suggest mitigation if critically low for large tmp files.

Deliverables
- Files in your workdir: requirements_frozen.txt, pip_list.txt, system_info.log, python_env_info.log, and any install logs.
- If major changes, update environment.md at project root accordingly.
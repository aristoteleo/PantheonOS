Project: Human immune-oncology gene profiling panel.
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager
Task: Capture computational environment and write environment.md at the project root.
- Collect OS, CPU, RAM, GPU, Python version, key Python packages relevant to scverse (python-igraph, scanpy, anndata, numpy, scipy, pandas, scikit-learn, statsmodels, matplotlib, seaborn, umap-learn, numba), and any available JAX/PyTorch if present.
- Note disk space in the workdir filesystem.
- If critical packages are missing for scRNA workflows, install them and record versions.
- Save a concise summary to environment.md in the project root.

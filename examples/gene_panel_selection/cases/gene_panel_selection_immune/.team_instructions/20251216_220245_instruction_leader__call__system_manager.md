Project: Human immune oncology gene profiling panel
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task:
1) Audit the computational environment and write a comprehensive environment.md in the project root. Include: OS, CPU cores/threads, RAM, GPU(s), Python version, pip/conda details, and the versions/presence of key packages for scRNA analysis and our workflow (anndata, scanpy, scikit-learn, pandas, numpy, scipy, matplotlib, seaborn, scvi-tools, numba, pygam, statsmodels, pynndescent, umap-learn). Also check availability of selection-related tools typically used by the selection_expert (e.g., SpaPROS, scGeneFit, and any dependencies they commonly need). If a package is not installed, note it explicitly.
2) If critical packages for scverse/selection are missing, install them in a way compatible with the current environment. Log what you install in environment.md and save any detailed install logs in your workdir.
3) Place environment.md at: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md
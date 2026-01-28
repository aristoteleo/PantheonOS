Project: Immune-oncology 1000-gene panel selection for human TME profiling
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task:
1) Audit the software and hardware environment and record the findings in environment.md at the PROJECT ROOT (not in workdir):
   - OS and version
   - Python version and path
   - CPUs, RAM, GPUs (and CUDA if available)
   - Available disk space in project/workdir
   - Installed Python packages relevant to scRNA/spatial analysis with versions
2) Ensure the following Python packages are installed (latest stable preferred). If missing, install them in the current environment and include final versions in environment.md:
   Core/scverse: anndata, scanpy, numpy, scipy, pandas, matplotlib, seaborn, statsmodels, scikit-learn
   Spatial: squidpy
   Methods for panel selection: spapros, scgenefit
   Utilities/plots: matplotlib-venn or upsetplot, tqdm, pynndescent, umap-learn
   Optional (if feasible): scvi-tools, shap, xgboost
3) Save a concise summary of key environment info at: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/environment_summary.txt for downstream agents to read quickly.

Notes:
- Do not modify any data files; only install packages and write the environment files.
- Return paths to environment.md and the summary file when done.
Project: Human immune oncology panel (1000 genes)
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task: 1) Audit the computational environment and 2) ensure the core scverse stack is available.

Please:
- Detect hardware and OS: CPU cores/threads, RAM, available disk at project workdir, GPU(s) and driver/CUDA if present.
- Record software: Python version, and versions of key packages: numpy, scipy, pandas, anndata, scanpy, scikit-learn, umap-learn, pynndescent, numba, squidpy, matplotlib, seaborn, statsmodels.
- Write a concise environment summary to: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md

Then:
- Verify the following minimal requirements are installed and importable:
  anndata, scanpy, scikit-learn, umap-learn, numpy, pandas, scipy, matplotlib, seaborn.
- If any are missing, install them in the current environment. Log actions and versions to a text log at your workdir (install_log.txt). If everything is present, just note that in the log.
- Do not modify the dataset; this step is only environment preparation.

Return paths to environment.md and any logs you created.
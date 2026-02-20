Goal: Capture the current computational environment and ensure needed packages for scRNA-seq panel selection are available.

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/workdir/system_manager

Tasks:
1) Audit environment (hardware CPU/GPU/memory, OS, Python/R versions, key Python packages and versions). Write a concise summary to environment.md in the project workdir.
2) Verify presence of core scverse stack and utilities commonly used by selection_expert (scanpy, anndata, numpy, pandas, scikit-learn, numba, seaborn, matplotlib, statsmodels, harmonypy, celltypist, squidpy, pytorch if available). Do not install unless missing.
3) If any of the above are missing, install them in a minimally invasive way. After installation, append to environment.md which packages were installed and versions.
4) Confirm read access to the following inputs:
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/best_G_star.txt
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/scores_kidney_filtered.csv
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/do_not_touch_kidney_backup/preliminary_results/adata_kidney.h5ad
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/kidney/preliminary_results/kidney_batches
Create a short accessibility report in environment.md.
5) Return a short status note file system_status.md in your agent workdir summarizing what you found/installed.
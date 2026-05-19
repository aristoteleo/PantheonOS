Task: Environment audit and setup for Gene Panel Selection project

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Sub-agent workdir (system_manager): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager
Root dir for environment.md: /home/erwinpi/pantheon-agents/examples/gene_panel_selection

Goals:
1) Write environment.md at the repository root with:
   - OS, CPU, GPU, RAM
   - Python version, virtual env details
   - Key package versions
2) Ensure/install required packages for scRNA-seq and gene panel selection workflows:
   - scanpy, anndata>=0.9, numpy, pandas, scipy, scikit-learn, matplotlib, seaborn, igraph, leidenalg, pyarrow
   - scvi-tools (optional if GPU available), statsmodels
   - squidpy (optional)
   - scgenefit, spapros
3) Produce frozen environment files in system_manager workdir:
   - requirements_frozen.txt (pip freeze)
   - conda list or pip list output

Constraints:
- Non-interactive automated setup. If installation is needed, perform it and record changes in environment.md

Outputs:
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/environment.md
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/requirements_frozen.txt
- /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager/pip_list.txt
- Any installation logs saved under the system_manager subdir.

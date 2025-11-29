Project: Mouse brain receptor profiling panel selection
Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/system_manager

Tasks:
1) Create environment.md at the project root describing the computational environment (OS, CPU/RAM, Python version, pip/conda, available GPUs, disk space). Save to: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/mouse_brain/workdir/environment.md

2) Ensure required Python packages are installed for this project:
   - abc_atlas_access[notebooks] from the Allen Institute GitHub (install via pip VCS URL):
     pip install "abc_atlas_access[notebooks] @ git+https://github.com/alleninstitute/abc_atlas_access.git"
   - scverse stack: scanpy, anndata, numpy, scipy, pandas, seaborn, matplotlib, scikit-learn, numba
   - plotting/utility: umap-learn, pynndescent, statsmodels, requests, pyarrow

   Please perform installation in the current Python environment. After installation, verify imports:
   - import abc_atlas_access as abc
   - import scanpy as sc; import anndata as ad
   - import numpy as np; import pandas as pd

   Record the final list of installed package versions for the above libraries in environment.md.

3) Create a short README.md in the project root with a timestamp and a brief note that the environment is prepared and abc_atlas_access is available.

Please proceed autonomously and write outputs into the specified workdir paths.
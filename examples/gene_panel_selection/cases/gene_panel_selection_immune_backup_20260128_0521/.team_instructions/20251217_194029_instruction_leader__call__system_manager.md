Project: Human immune-oncology gene panel selection (1000 genes)
Workdir (project root): /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Your workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Task 1: Capture computational environment
- Probe OS, CPU, RAM, disk space, Python version, CUDA/GPU availability, and locale.
- List key Python packages and versions if installed: anndata, scanpy, squidpy, numpy, scipy, pandas, scikit-learn, umap-learn, matplotlib, seaborn, h5py, pynndescent, numba, torch, rapids (if any), statsmodels.
- Save a concise report to: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/environment.md

Task 2: Sanity checks for dataset accessibility
- Confirm that the dataset file exists and is readable:
  /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Report file size and last modified time.
- Add this info to environment.md under a section "Dataset accessibility".

Task 3: (If trivial) ensure basic deps are available
- If Python essentials for scverse workflows are missing (scanpy/anndata/numpy/scipy/pandas/h5py/umap-learn/scikit-learn), install them in the current environment, and append the versions to environment.md.

Output only the environment.md file in the project root. Save any scratch outputs in your workdir if needed.
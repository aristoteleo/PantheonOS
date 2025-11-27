Project setup and environment capture for PBMC3k gene panel selection

Context:
- Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k
- Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir
- Input AnnData path: /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad

Tasks:
1) Workspace preparation
- Create the workdir if missing.
- Create subdirectories inside workdir: selection_expert, biologist, reporter, logs.
- List the contents of the workdir and note any existing results/figures/notes. If anything exists, write a short summary of existing results in a file named notes_<timestamp>.md inside the workdir.

2) Environment capture (root-level file)
- Check at project root for environment.md. If it does not exist, create it and populate with:
  * OS and version, CPU info, total RAM.
  * Python version and environment name (conda/venv/system).
  * pip freeze (or conda list) of key packages.
  * GPU availability (if any).
- Verify the following Python packages are available; if missing, install them in the active environment:
  scanpy>=1.9, anndata, numpy, scipy, pandas, scikit-learn, matplotlib, seaborn, statsmodels, scgeneFit, spapros
  (optional but helpful: umap-learn, pynndescent, louvain or leidenalg).
- Append the resulting package versions to environment.md.

3) Data availability check
- Verify that the AnnData file is accessible at /home/erwinpi/Vizgen/pbmc3k_preprocessed.h5ad and report its size and last-modified time in notes_<timestamp>.md.

Output expectations:
- Ensure the following paths exist:
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/selection_expert
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/biologist
  /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/pbmc3k/workdir/reporter
- A root-level environment.md updated/created.
- A workdir-level notes_<timestamp>.md that documents existing contents (if any) and the data availability check.

Please reply with the created/verified directories and the path of the latest notes_<timestamp>.md.

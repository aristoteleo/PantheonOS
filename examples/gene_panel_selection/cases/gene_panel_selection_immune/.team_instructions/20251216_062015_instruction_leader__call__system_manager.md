Task: Create environment.md with hardware/software inventory and ensure required packages for scRNA-seq panel selection are available.

Project workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir
Agent workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

Context:
- We will run scverse-based workflows (scanpy/anndata, numpy/scipy, scikit-learn), plotting (matplotlib/seaborn), and typical panel-selection dependencies (SpaPROS, scGeneFit, pandas, umap-learn, pynndescent). PDF rendering for final report.
- Dataset path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad

Actions:
1) Detect OS, CPU/GPU, RAM, Python version, key library versions. Write a detailed environment.md in project root.
2) Verify installation of: python>=3.9, scanpy, anndata, numpy, scipy, pandas, scikit-learn, umap-learn, matplotlib, seaborn, pynndescent, statsmodels, numba; plus SpaPROS and scGeneFit if available. If missing, install them or note constraints.
3) Check we can import the above and open the provided .h5ad (read-only test). Do not modify the dataset.
4) Summarize any constraints that would require downsampling or gene subsetting.
5) Save console logs to system_manager/setup_log.txt
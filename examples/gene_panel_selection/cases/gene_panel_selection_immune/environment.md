# Environment Audit: Immune-oncology 1000-gene panel selection for human TME profiling

Timestamp (UTC): 2025-11-27T04:59:34.724404Z

Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
System manager workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir/system_manager

## OS
- Pretty name: Ubuntu 22.04.5 LTS (Jammy Jellyfish)
- Kernel: Linux 5.15.0-161-generic x86_64

## Python
- Version: 3.10.19 (main, Oct 21 2025, 16:43:05) [GCC 11.2.0]
- Executable: /home/erwinpi/miniconda3/envs/gps/bin/python
- Prefix: /home/erwinpi/miniconda3/envs/gps
- Pip: pip 25.3 at /home/erwinpi/miniconda3/envs/gps/lib/python3.10/site-packages/pip (python 3.10)

## CPU
- Logical CPUs: 56
- Model: Intel(R) Xeon(R) Platinum 8280 CPU @ 2.60GHz
- NUMA nodes: 2 (0-27, 28-55)

## Memory
- MemTotal: 1584966256 kB (~1511.9 GB)
- MemFree: 894024372 kB (~852.7 GB)
- MemAvailable: 1569710936 kB (~1497.9 GB)

## Disk space
- Project root: total 10158.19 GB, used 8355.33 GB, free 1290.85 GB
- Project workdir: total 10158.19 GB, used 8355.33 GB, free 1290.85 GB
- System manager workdir: total 10158.19 GB, used 8355.33 GB, free 1290.85 GB

## GPU/CUDA
- nvidia-smi: /usr/bin/nvidia-smi (not usable: returned status 9)
- nvcc: not found
- CUDA: not available/active for this session

## Installed Python packages (key relevant)
- anndata: 0.11.4
- scanpy: 1.11.5
- numpy: 2.2.6
- scipy: 1.15.3
- pandas: 2.3.3
- matplotlib: 3.10.7
- seaborn: 0.13.2
- statsmodels: 0.14.5
- scikit-learn: 1.7.2
- squidpy: 1.6.5
- spapros: 0.1.5
- scGeneFit: (installed, version attribute not provided)
- matplotlib-venn: 1.1.2
- upsetplot: 0.9.0
- tqdm: 4.67.1
- pynndescent: 0.5.13
- umap-learn: 0.5.9.post2
- scvi-tools: 1.3.3
- shap: 0.49.1
- xgboost: 3.1.2

## Notes on installation actions taken
- Installed matplotlib-venn (latest) to support set visualizations.
- Installed scvi-tools and shap as optional packages. pip reported potential dependency conflicts, but both imported successfully post-install.
- All other required packages were already present.


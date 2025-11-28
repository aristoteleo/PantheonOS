Computational Environment Summary – Human immune-oncology gene profiling panel

Generated: 2025-11-28 (UTC)
Project root: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune
Workdir: /home/erwinpi/pantheon-agents/examples/gene_panel_selection/cases/gene_panel_selection_immune/workdir

System
- OS: Ubuntu 22.04.5 LTS (Linux-5.15.0-161-generic x86_64; glibc 2.35)
- CPU: Intel(R) Xeon(R) Platinum 8280 CPU @ 2.60GHz
  - Logical cores: 56; Estimated physical cores: 56
- RAM: 1511.54 GB
- GPU: nvidia-smi present; no NVIDIA GPUs visible to the system

Python
- Version: 3.10.19
- Executable: /home/erwinpi/miniconda3/envs/gps/bin/python

Key Python packages (scverse-related)
- python-igraph: 0.11.9
- scanpy: 1.11.5
- anndata: 0.11.4
- numpy: 1.26.4
- scipy: 1.15.3
- pandas: 2.3.3
- scikit-learn: 1.7.2
- statsmodels: 0.14.5
- matplotlib: 3.10.7
- seaborn: 0.13.2
- umap-learn: 0.5.9.post2
- numba: 0.62.1
Additional/adjacent packages
- scikit-image: 0.25.2
- scikit-misc: 0.0.0
- squidpy: 1.6.5
- harmonypy: 0.0.10
- moscot: 0.4.3 (installed, but import currently fails due to ott.neural API change; see note)

ML frameworks
- PyTorch: 2.9.1+cu128; CUDA available: False
- JAX: 0.6.2; jaxlib: 0.6.2; devices: CPU

Storage (filesystem hosting workdir)
- Total: 10907.27 GB; Used: 8980.86 GB; Free: 1376.64 GB

Notes
- Moscot import currently fails with: "cannot import name 'datasets' from 'ott.neural'". This indicates a version mismatch between moscot and ott/ott-jax. Moscot is optional for standard scRNA-seq workflows; if needed, consider upgrading moscot and ott-jax together (e.g., pip install -U moscot ott-jax) or pinning compatible versions.
- All other core scRNA analysis packages are installed.

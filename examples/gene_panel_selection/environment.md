Environment audit for Gene Panel Selection project

Date: 2025-11-25

System summary
- OS: Ubuntu 22.04.5 LTS (Linux 6.8.0-87-generic)
- CPU: 2 x AMD EPYC 9224 (total 96 vCPUs)
- RAM: 188 GiB (approx.), available ~163 GiB at audit time
- Disk: Root / 879G total, 784G used, 51G free (94% used) – consider freeing space
- GPUs: 2 x NVIDIA H100 PCIe, Driver 570.172.08, CUDA reported by driver 12.8
- CUDA toolkit (nvcc): 11.5 detected

Python/conda environment
- Python: 3.10.19
- Conda: 25.9.1 (env: gps)
- Environment location: /home/erwinpi/miniconda3/envs/gps
- Interpreter: /home/erwinpi/miniconda3/envs/gps/bin/python
- VIRTUAL_ENV: empty (using conda, not venv)
- site-packages: /home/erwinpi/miniconda3/envs/gps/lib/python3.10/site-packages

GPU/ML frameworks
- PyTorch: 2.9.1+cu128
  - CUDA available: True
  - CUDA version (torch): 12.8
- JAX/JAXLIB: 0.6.2/0.6.2 (CPU fallback noted; no CUDA-enabled jaxlib present)

Key Python packages for scRNA-seq and gene panel selection
- scanpy: 1.11.5
- anndata: 0.11.4 (>=0.9 OK)
- numpy: 2.2.6
- pandas: 2.3.3
- scipy: 1.15.3
- scikit-learn: 1.7.2
- scikit-image: 0.25.2
- scikit-misc: 0.5.2
- matplotlib: 3.10.7
- seaborn: 0.13.2
- igraph: 1.0.0
- leidenalg: 0.11.0
- pyarrow: 22.0.0
- statsmodels: 0.14.5
- scvi-tools: 1.3.3 (GPU-capable via torch)
- squidpy: 1.6.5 (optional)
- moscot: 0.4.3
- scGeneFit: installed (package provides no __version__)
- spapros: 0.1.5

Actions taken (install/upgrade)
- Installed/updated via pip:
  scikit-learn, scikit-image, scikit-misc, squidpy, harmonypy, moscot, pyarrow, scvi-tools, scGeneFit
- Resolved moscot import error by pinning ott-jax to 0.5.1 (pip install ott-jax==0.5.1) after initial install brought in ott-jax 0.6.0 which was incompatible.

Files generated
- Frozen requirements: cases/gene_panel_selection_immune/workdir/system_manager/requirements_frozen.txt
- Pip list (human-readable): cases/gene_panel_selection_immune/workdir/system_manager/pip_list.txt
- System info log: cases/gene_panel_selection_immune/workdir/system_manager/system_info.log
- Python env info log: cases/gene_panel_selection_immune/workdir/system_manager/python_env_info.log
- Pip install logs (time-stamped): cases/gene_panel_selection_immune/workdir/system_manager/pip_install_*.log

Notes
- Disk free space on / is low (~51G free, 94% used). For large datasets and temporary files, consider cleaning up or redirecting caches (e.g., TMPDIR) to a larger volume.
- JAX is operating on CPU due to missing CUDA-enabled jaxlib; this is acceptable for moscot unless GPU acceleration is needed for JAX components.

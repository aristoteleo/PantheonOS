Environment summary update (2025-12-17)

Kernel and Python
- Python: 3.10.19 (/home/erwinpi/miniconda3/envs/gps/bin/python)
- ipykernel: 7.1.0
- Jupyter kernelspec: /home/erwinpi/miniconda3/envs/gps/share/jupyter/kernels/python3/kernel.json

Requested single-cell stack (all satisfied)
- scanpy: 1.11.5
- anndata: 0.11.4
- numpy: 2.2.6
- scipy: 1.15.3
- pandas: 2.3.3
- matplotlib: 3.10.7
- seaborn: 0.13.2
- scikit-learn: 1.7.2
- umap-learn: 0.5.9.post2
- pynndescent: 0.5.13
- numba: 0.62.1
- statsmodels: 0.14.5
- python-igraph: 0.11.9
- louvain: 0.8.2
- leidenalg: 0.11.0
- harmonypy: 0.0.10 (optional)

Hardware snapshot
- CPU: 56 vCPUs (Intel Xeon Platinum 8280), NUMA 2 nodes
- Memory: ~1.5 TiB total, ~1.4 TiB available at check time
- Disk: root 10T (8.4T used, 1.1T free)
- GPUs: 4x NVIDIA A100 40GB, driver 570.133.20, CUDA 12.8

Notes
- All versions meet or exceed requested minimums. `louvain` emits a deprecation warning due to pkg_resources, but works.

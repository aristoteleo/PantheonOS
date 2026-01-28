Environment report for gene_panel_selection_immune

Last updated: 2025-12-16T06:24:12Z
Host: tag-308 (Ubuntu 22.04.5 LTS, kernel 5.15.0-161-generic)

Hardware
- CPU: 56 vCPU (2 sockets x 28 cores, Intel Xeon Platinum 8280 @ 2.60GHz; AVX2/AVX-512 available; SMT disabled)
- RAM: ~1.5 TiB total; ~1.1 TiB free at measurement
- Disk: root / 10T total, 8.4T used, ~1.1T free (89% used). Boot partitions nearly empty. NFS mount available (12T total, 2.6T free).
- GPU: 4 x NVIDIA A100-PCIE-40GB (Driver 570.133.20, CUDA 12.8). No GPU processes active at measurement.

Software
- Python: 3.10.19 (/home/erwinpi/miniconda3/envs/gps/bin/python3)
- Platform: Linux-5.15.0-161-generic-x86_64-with-glibc2.35

Key Python packages
- scanpy: 1.11.5
- anndata: 0.11.4
- numpy: 2.2.6
- scipy: 1.15.3
- pandas: 2.3.3
- scikit-learn: 1.7.2
- umap-learn: 0.5.9.post2
- matplotlib: 3.10.7 (PDF backend available)
- seaborn: 0.13.2
- pynndescent: 0.5.13
- statsmodels: 0.14.5
- numba: 0.62.1 (numba.cuda available: False)
- squidpy: 1.6.5
- harmonypy: 0.0.10
- moscot: 0.5.0
- scikit-image: 0.25.2
- scikit-misc: 0.0.0
- SpaPROS (spapros): 0.1.5
- scGeneFit: 1.0.0

Dataset check
- Path: /home/erwinpi/Vizgen/6039d13f-0c3e-484b-b37c-ee3656c4c037.h5ad
- Exists: yes
- Read test: success using anndata.read_h5ad(backed='r')
- Shape: 355,941 cells × 22,781 genes (n_obs=355,941; n_vars=22,781)

PDF/reporting
- Matplotlib PDF backend available; PDF report generation is supported.

Notes and constraints
- CPU and memory are ample for full-resolution analysis; dense X of this size is ~8.1e9 values (~32 GB as float32) plus overhead. However, end-to-end Scanpy workflows (neighbors/UMAP/clustering) on ~356k cells can be time-consuming; consider using pynndescent (installed) and appropriate n_neighbors/min_dist to balance speed/quality.
- numba CUDA is not available (even though GPUs are present); workflows will run on CPU. GPU acceleration for UMAP/NN is not configured.
- Disk on / is 89% utilized but ~1.1T free; ensure large temporary files (e.g., intermediate .h5ad or figures) fit within available space.

Installation actions
- All requested packages are already installed and importable. No installations were needed.

Logs
- Detailed setup logs: workdir/system_manager/setup_log.txt

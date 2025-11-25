Environment audit for: Integrative analysis of human heart spatial transcriptomics (MERFISH 2D/3D, pcw12) with human fetal heart scRNA-seq (10x Multiome RNA) and CHD gene list

Date: 2025-11-21

Paths
- Project root (this file): /home/wzxu/software/pantheon-agents/examples/single_cell_spatial_analysis
- Workdir: /home/wzxu/software/pantheon-agents/examples/single_cell_spatial_analysis/cases/chd_heart_spatial/workdir
- Logs: workdir/system_manager

System (OS/Hardware)
- OS: Ubuntu 22.04.5 LTS (Jammy), Kernel: 6.8.0-87-generic
- CPU: 2x AMD EPYC 9224 (24 cores/socket, 48C/96T total). AVX2 + AVX-512 supported
- RAM: 188 GiB total; ~168 GiB available at audit time
- Swap: 2 GiB (fully used). Note: very small; rely on RAM
- GPU: 2x NVIDIA H100 PCIe, 80 GB each (CUDA runtime 12.8 via driver); GPU idle at audit time
- CUDA toolkit on PATH: nvcc 11.5 (toolkit is older than driver; OK since we use prebuilt wheels)
- Filesystem free space for project root (/): 69 GiB free of 879 GiB total

Python environment
- Interpreter: /home/wzxu/.local/share/mamba/envs/pantheon/bin/python
- Version: 3.10.18 (conda-forge build)
- Active env: CONDA_PREFIX=/home/wzxu/.local/share/mamba/envs/pantheon (conda/mamba command not available in shell; using pip for changes)

GPU/ML stack
- PyTorch: 2.9.0+cu128 (CUDA 12.8), torch.cuda.is_available: True
- scvi-tools: 1.3.3

Key single-cell/spatial packages
- scanpy: 1.11.5
- anndata: 0.11.4
- squidpy: 1.6.2
- tangram (tangram-sc): 1.0.4
- cell2location: 0.1.5
- decoupler: 2.1.1
- gseapy: 1.1.10
- harmonypy: 0.0.10
- moscot: 0.4.3 (fixed by pinning ott-jax==0.5.0)
- igraph/python-igraph: 0.11.5
- leidenalg: 0.10.2
- scikit-learn: 1.7.2
- scikit-image: 0.25.2
- scikit-misc: 0.5.1
- numba: 0.61.2
- numpy: 1.26.4
- scipy: 1.15.3
- pandas: 2.3.3
- matplotlib: 3.8.4
- seaborn: 0.13.2
- pyarrow: 21.0.0
- zarr: 2.18.3
- h5py: 3.11.0
- Optional: scanorama not installed

Jupyter
- JupyterLab 4.4.7 installed; ipywidgets 8.1.7 present
- Classic jupyter_contrib_nbextensions is incompatible with Notebook 7+/Lab 4 and failed to install. Use JupyterLab’s built-in extension manager or pip-installable lab extensions instead (e.g., jupyterlab-git, jupyterlab-lsp). ipywidgets works in Lab 4 by default

Changes made during setup
- Resolved moscot import error by downgrading ott-jax to 0.5.0 so that moscot 0.4.3 can import ott.neural.datasets
  - pip install ott-jax==0.5.0
- Attempted to install classic nbextensions; skipped due to incompatibility with Notebook 7

Large data handling (10.5 GB and 6.0 GB .h5ad)
- RAM: Sufficient (≥168 GiB free) to load these files fully if needed, but prefer memory-mapped/backed mode to reduce peak memory
- Disk: Only ~69 GiB free on /. This is a constraint for writing intermediate results or multiple copies of large AnnData. Avoid unnecessary copies and prefer incremental/backed writes

Recommended configuration for stability and performance
- Backed mode and memory mapping
  - Use anndata.read_h5ad(path, backed="r") when inspection is sufficient
  - For writing without full materialization: use backed="r+" or write directly to new file with AnnData.write_h5ad(compression="lzf")
  - Prefer sparse layers (csr/csc) when possible before writing; avoid densifying .X
- Temporary directories (avoid filling /tmp and the 69 GiB root)
  - Create and use a dedicated tmp directory in the case workdir:
    - export TMPDIR=/home/wzxu/software/pantheon-agents/examples/single_cell_spatial_analysis/cases/chd_heart_spatial/workdir/tmp
    - export JOBLIB_TEMP_FOLDER="$TMPDIR"
    - export MPLCONFIGDIR="$TMPDIR/mpl"
    - export DASK_TEMPORARY_DIRECTORY="$TMPDIR"  # if using dask via squidpy/spatialdata
  - Ensure directory exists before runs
- Parallelism and BLAS threads (avoid oversubscription on 96 HW threads)
  - Suggested defaults for single jobs:
    - export OMP_NUM_THREADS=24
    - export OPENBLAS_NUM_THREADS=24
    - export MKL_NUM_THREADS=24
    - export NUMEXPR_NUM_THREADS=24
    - export NUMBA_NUM_THREADS=24
  - Increase to 48 if a single job needs more parallelism, but avoid 96 unless necessary
  - For scanpy.pp.neighbors and UMAP, also consider n_jobs parameters explicitly
- GPU usage
  - For scvi-tools/cell2location/tangram, prefer GPU: set CUDA_VISIBLE_DEVICES to select an H100 (0 or 1)
  - torch is built for CUDA 12.8 and detects both GPUs
- HDF5
  - On local NVMe, default file locking is fine. If using network FS in the future, consider: export HDF5_USE_FILE_LOCKING=FALSE

Operational guidance for these datasets
- 10.5 GB 3D MERFISH .h5ad
  - Reading in backed mode (r) uses ~100–300 MB RAM; converting to dense or computing highly variable genes may materialize large matrices. Keep matrices sparse and subset to HVGs before graph building
  - When computing neighbors/UMAP on very large matrices, expect several tens of GB of RAM. Consider PCA with svd_solver="arpack" or randomized and limit n_components to 50–100
  - If writing results, use ad.write_h5ad(..., compression="lzf") to reduce size and speed I/O
- 6.0 GB scRNA-seq .h5ad
  - Similar considerations; GPU-accelerated scVI models are recommended. Batch size tuning may be required to fit GPU memory; H100 80 GB is ample
- Disk space caution
  - With only ~69 GiB free on /, avoid creating multiple full copies of the 10.5 GB file. Prefer in-place backed editing or write outputs to a single consolidated file. If possible, free up space or mount additional storage for intermediate artifacts and caches

Quick test snippet (backed reading)
- import anndata as ad
- ad3d = ad.read_h5ad("/home/wzxu/.../merfish_human_heart_3d.h5ad", backed="r")
- print(ad3d.shape, ad3d.isbacked)

Package/version table (from audit)
- See the lists above. Full logs with exact versions and tool outputs: workdir/system_manager/hw_os_audit.log and python_tools_audit.log

Summary
- Ready for analysis, with the following constraints/notes:
  - Disk headroom is low (~69 GiB free). Use backed mode and minimize duplicates; consider freeing space or providing a larger scratch for heavy intermediate outputs
  - Classic jupyter_contrib_nbextensions is not supported with Notebook 7/Lab 4; use JupyterLab extensions instead (ipywidgets already works)
  - moscot dependency was corrected by pinning ott-jax==0.5.0

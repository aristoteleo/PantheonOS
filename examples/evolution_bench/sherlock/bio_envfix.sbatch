#!/bin/bash
# Env fixes after the first pilot (molt_biology.md): umap-learn 0.6.0 breaks scib-metrics 0.6.2
# (numba wants C-contiguous knn_dists) -> pin 0.5.7; annoy was compiled -march=native on a Zen4 node and
# dies with SIGILL on the EPYC 7543 servers -> rebuild it here, on a 7543.
#SBATCH --job-name=bio-envfix
#SBATCH --partition=normal
#SBATCH --constraint=CPU_SKU:7543
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=00:45:00
#SBATCH --output=/scratch/users/wzxu/evolve/logs/%x-%j.out
set -euo pipefail
EVO=$SCRATCH/evolve; ENVS=$GROUP_HOME/wzxu
ulimit -n $(ulimit -Hn)
apptainer exec --bind /scratch,/home/groups "$EVO/py312full.sif" bash -c "
  set -e; ulimit -n \$(ulimit -Hn)
  export UV_CACHE_DIR=$SCRATCH/.uv-cache UV_PYTHON_PREFERENCE=only-system UV_LINK_MODE=copy
  VIRTUAL_ENV=$ENVS/venv-bio $EVO/bin/uv pip install -q 'umap-learn==0.5.7'
  VIRTUAL_ENV=$ENVS/venv-bio-ref $EVO/bin/uv pip install -q 'umap-learn==0.5.7'
  VIRTUAL_ENV=$ENVS/venv-bio-ref $EVO/bin/uv pip install -q --reinstall-package annoy --no-cache annoy
  for v in venv-bio venv-bio-ref; do VIRTUAL_ENV=$ENVS/\$v $EVO/bin/uv pip freeze > $ENVS/\$v.freeze.txt; done"
apptainer exec --bind /scratch,/home/groups "$EVO/py312.sif" "$ENVS/venv-bio-ref/bin/python" -c "
import numpy as np, scipy.sparse as sp, scanorama, umap
x = [sp.csr_matrix(np.random.rand(n, 50).astype('float32')) for n in (200, 150)]
z, _ = scanorama.integrate(x, [list(map(str, range(50)))] * 2, dimred=20, verbose=0)
print('scanorama ok', z[0].shape, 'umap', umap.__version__)"
echo "[$(date)] done"

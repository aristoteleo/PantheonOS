#!/bin/bash
# One-time environment setup on Sherlock (login node). Idempotent.
# Sherlock is CentOS 7 (glibc 2.17, gcc 4.8): modern wheels do not install natively, so the
# environment is built and run inside a python:3.12-slim Apptainer image. Everything lives on
# $SCRATCH (HOME has a 15 GB quota). Usage: bash setup_env.sh [repo_dir]
set -euo pipefail
REPO=${1:-$SCRATCH/evolve/PantheonOS}
EVO=$SCRATCH/evolve
export APPTAINER_CACHEDIR=$SCRATCH/.apptainer-cache APPTAINER_TMPDIR=$SCRATCH/.apptainer-tmp
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" "$EVO/results" "$EVO/bin"
[ -x "$EVO/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=$EVO/bin UV_NO_MODIFY_PATH=1 sh
[ -f "$EVO/py312.sif" ] || apptainer pull "$EVO/py312.sif" docker://python:3.12-slim
# Login nodes hard-cap open files at 256 (uv extraction fails); build from a compute node.
srun -p xiaojie,dev -c 4 --mem=8G -t 15 apptainer exec --bind /scratch "$EVO/py312.sif" bash -c "
  ulimit -n \$(ulimit -Hn)
  export UV_CACHE_DIR=$SCRATCH/.uv-cache UV_PROJECT_ENVIRONMENT=$EVO/venv UV_PYTHON=/usr/local/bin/python3 UV_PYTHON_PREFERENCE=only-system UV_LINK_MODE=copy
  cd $REPO && $EVO/bin/uv sync --frozen --no-install-project 2>&1 | tail -3
  export PYTHONPATH=$REPO
  $EVO/venv/bin/python -c 'import numpy, scipy, litellm, pantheon.evolution; print(\"env ok\")'
  $EVO/venv/bin/python examples/evolution_bench/run_bench.py --help > /dev/null && echo 'run_bench --help ok'"

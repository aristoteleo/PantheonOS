#!/bin/bash
# Step 1 of molt_biology.md: build the task inputs (once) and run the pilot reads, on the CPU type
# the eval servers will use.
#SBATCH --job-name=bio-pilot
#SBATCH --partition=normal
#SBATCH --constraint=CPU_SKU:7543
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/users/wzxu/evolve/logs/%x-%j.out
set -uo pipefail
EVO=$SCRATCH/evolve; BIO=$EVO/biodata; PY=$GROUP_HOME/wzxu/venv-bio-ref/bin/python
T=$EVO/PantheonOS-hoff/examples/evolution_bench/tasks
echo "[$(date)] host=$(hostname) $(grep -m1 'model name' /proc/cpuinfo)"
ulimit -n $(ulimit -Hn)
run() { apptainer exec --bind /scratch,/home/groups,/lscratch --env XDG_CACHE_HOME=$SCRATCH/.cache,NUMBA_CACHE_DIR=$SCRATCH/.cache/numba,EVAL_TMP=$L_SCRATCH_JOB "$EVO/py312.sif" "$PY" "$@"; }
[ -s "$BIO/prep/prep_info.json" ] || run "$T/_bio/prep_bio.py" "$BIO" "$BIO/prep" || { echo "prep failed"; exit 1; }
run "$T/_bio/pilot_bio.py" "$BIO/pilot.json" "$@"
echo "[$(date)] done"


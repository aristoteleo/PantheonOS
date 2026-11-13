#!/bin/bash
# Final measurement of the single-cell main runs (molt_biology.md, Analysis step 1), one task per
# array index, on the CPU type of the eval servers. Submit once, held until every arm has ended:
#   sbatch --array=0-1 --dependency=afterany:<arms array job> bio_measure.sbatch [RESULTS_ROOT] [N_RUNS]
# If a run of the root lacks summary.json the job stops without reading anything (find the cause
# first). Otherwise it frees the eval servers (no search is left to use them) and reads valid and
# test. Then, on a login node: stats_bio.py RESULTS_ROOT $BIO/pilot.json
#SBATCH --job-name=bio-measure
#SBATCH --partition=normal
#SBATCH --constraint=CPU_SKU:7543
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=24:00:00
#SBATCH --output=/scratch/users/wzxu/evolve/logs/%x-%A_%a.out
set -uo pipefail
EVO=$SCRATCH/evolve; PY=$GROUP_HOME/wzxu/venv-bio/bin/python
ROOT=${1:-$EVO/results/biomain}; NEED=${2:-50}
TASKS=(bio_integration bio_denoise); TASK=${TASKS[$SLURM_ARRAY_TASK_ID]}
T=$EVO/PantheonOS-hoff/examples/evolution_bench/tasks
echo "[$(date)] host=$(hostname) task=$TASK root=$ROOT $(grep -m1 'model name' /proc/cpuinfo)"
HAVE=$(ls "$ROOT"/bio_*_s*/summary.json 2>/dev/null | wc -l)
if [ "$HAVE" -ne "$NEED" ]; then
  echo "only $HAVE of $NEED runs have summary.json; not measuring. Runs without one:"
  for d in "$ROOT"/bio_*_s*/; do [ -f "$d/summary.json" ] || echo "  $d"; done
  exit 1
fi
scancel -u "$USER" -n evolve-biosrv
ulimit -n $(ulimit -Hn)
apptainer exec --bind /scratch,/home/groups,/lscratch \
  --env PYTHONUNBUFFERED=1,TMPDIR=$L_SCRATCH_JOB,EVAL_TMP=$L_SCRATCH_JOB,XDG_CACHE_HOME=$SCRATCH/.cache,NUMBA_CACHE_DIR=$SCRATCH/.cache/numba \
  "$EVO/py312.sif" "$PY" "$T/_bio/measure_bio.py" "$ROOT" "$TASK" --reads=3 --top=5 --par=2
RC=$?; echo "[$(date)] exit $RC"; exit $RC

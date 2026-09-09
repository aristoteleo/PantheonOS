#!/bin/bash
# One bench arm per array task -- same spec format as modal_exp.py ({"out", "argv", "script"?}).
#   sbatch --array=0-$((N-1)) arm.sbatch SPEC.json        (or use submit.sh SPEC.json)
# A task whose output dir already holds store.json resumes (preemption-safe, like Modal).
#SBATCH --job-name=evolve-arm
#SBATCH --partition=xiaojie,normal
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=24:00:00
#SBATCH --output=logs/%x-%A_%a.out
set -uo pipefail
SPEC=$1
REPO=${REPO:-$SCRATCH/evolve/PantheonOS}
RESULTS=${RESULTS:-$SCRATCH/evolve/results}
EVO=$SCRATCH/evolve
export PYTHONPATH=$REPO PYTHONUNBUFFERED=1 UPSTREAM_ENGINE_DIR=$EVO/upstream_simpletes
ulimit -n $(ulimit -Hn)   # login nodes cap at 256; compute nodes allow 131072
set -a; . "$HOME/.evolve_env"; set +a
cd "$REPO"
read -r OUT SCRIPT URLFILE ARGV < <($HOME/miniconda3/bin/python3 - "$SPEC" "$SLURM_ARRAY_TASK_ID" <<'PY'
import json, shlex, sys
a = json.load(open(sys.argv[1]))[int(sys.argv[2])]
print(a["out"], a.get("script", "examples/evolution_bench/run_bench.py"), a.get("eval_server_file", "-"),
      " ".join(shlex.quote(str(x)) for x in a["argv"]))
PY
)
# AHC039 arms measure on a wave's eval server (eval_server.sbatch); wait for its URL file and health.
if [ "$URLFILE" != "-" ]; then
  until [ -f "$URLFILE" ] && curl -sf -m 10 "$(sed "s|/evaluate$|/health|" "$URLFILE")" >/dev/null; do sleep 30; done
  ARGV="$ARGV --eval-server $(cat "$URLFILE")"
fi
mkdir -p "$RESULTS/$OUT"
RESUME=""; [ -f "$RESULTS/$OUT/store.json" ] && [[ "$SCRIPT" == *run_bench.py ]] && RESUME="--resume"
echo "[$(date)] host=$(hostname) job=$SLURM_JOB_ID arm=$OUT script=$SCRIPT resume=${RESUME:-no}" | tee -a "$RESULTS/$OUT/run.log"
# glibc 2.17 on the host: run inside the python:3.12-slim image whose venv holds the deps
# (see setup_env.sh); the repo itself is on PYTHONPATH, so a resync is all an update needs.
eval apptainer exec --bind /scratch "$EVO/py312.sif" "$EVO/venv/bin/python" "$SCRIPT" $ARGV --output "$RESULTS/$OUT" $RESUME >> "$RESULTS/$OUT/run.log" 2>&1
RC=$?; echo "[$(date)] exit $RC" >> "$RESULTS/$OUT/run.log"; exit $RC

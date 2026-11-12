#!/bin/bash
# Eval server of a single-cell task (molt_biology.md):  sbatch eval_server_bio.sbatch <name> <port> <task>
# EPYC 7543 like the AHC re-measure; the single-cell env from $GROUP_HOME inside py312.sif. One request
# at a time (the server's lock): a program gets 8 threads, scib-metrics 8 jobs. URL -> $EVO/eval_server_<name>.url
#SBATCH --job-name=evolve-biosrv
#SBATCH --partition=normal
#SBATCH --constraint=CPU_SKU:7543
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/users/wzxu/evolve/logs/%x-%j.out
set -uo pipefail
NAME=$1; PORT=$2; TASK=$3
EVO=$SCRATCH/evolve
REPO=$EVO/PantheonOS-hoff
ulimit -n $(ulimit -Hn)
set -a; . "$HOME/.evolve_env"; set +a
rm -f "$EVO/eval_server_$NAME.url"
echo "[$(date)] bio eval server $NAME task=$TASK host=$(hostname) port=$PORT job=$SLURM_JOB_ID $(grep -m1 'model name' /proc/cpuinfo)"
exec apptainer exec --bind /scratch,/home/groups,/lscratch \
  --env PYTHONUNBUFFERED=1,EVAL_TMP=$L_SCRATCH_JOB,EVAL_WARMUP_TASK=$TASK,BIO_SPLIT=${BIO_SPLIT:-public},XDG_CACHE_HOME=$SCRATCH/.cache,NUMBA_CACHE_DIR=$SCRATCH/.cache/numba \
  "$EVO/py312.sif" "$GROUP_HOME/wzxu/venv-bio/bin/python" \
  "$REPO/examples/evolution_bench/sherlock/eval_server_local.py" --port "$PORT" --url-file "$EVO/eval_server_$NAME.url"

#!/bin/bash
# One AHC039 eval server = one wave's ruler.   sbatch eval_server.sbatch <wave> <port>
# A whole 32-core `normal` node to itself (--exclusive) inside the ale-bench image (g++-12); the URL lands in
# $EVO/eval_server_<wave>.url for the arms of that wave.
#SBATCH --job-name=evolve-evalsrv
#SBATCH --partition=xiaojie
#SBATCH --nodelist=sh04-05n13

#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=logs/%x-%j.out
set -uo pipefail
WAVE=$1; PORT=${2:-8001}
EVO=$SCRATCH/evolve
export PYTHONPATH=$EVO/PantheonOS PYTHONUNBUFFERED=1 AHC_EXEC=direct AHC_CASE_WORKERS=24 EVAL_TMP=/tmp
ulimit -n $(ulimit -Hn)
set -a; . "$HOME/.evolve_env"; set +a
rm -f "$EVO/eval_server_$WAVE.url"
echo "[$(date)] eval server wave=$WAVE host=$(hostname) port=$PORT job=$SLURM_JOB_ID"
exec apptainer exec --bind /scratch --env PYTHONPATH=$EVO/PantheonOS,AHC_EXEC=direct,AHC_CASE_WORKERS=24,EVAL_TMP=/tmp,PYTHONUNBUFFERED=1 "$EVO/alebench.sif" "$EVO/venv-ahc/bin/python" \
  "$EVO/PantheonOS/examples/evolution_bench/sherlock/eval_server_local.py" --port "$PORT" --url-file "$EVO/eval_server_$WAVE.url"

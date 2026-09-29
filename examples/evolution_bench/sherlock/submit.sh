#!/bin/bash
# submit.sh SPEC.json [extra sbatch args]  -> one array job, one task per arm; prints the job id.
set -euo pipefail
SPEC=$(readlink -f "$1"); shift || true
N=$($HOME/miniconda3/bin/python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$SPEC")
mkdir -p "$(dirname "$0")/logs"
cd "$(dirname "$0")"
sbatch --parsable --array=0-$((N-1)) "$@" arm.sbatch "$SPEC" | tee -a submitted.log

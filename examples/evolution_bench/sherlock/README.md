# Running bench arms on Sherlock

1. `bash setup_env.sh ~/PantheonOS` once (installs uv, syncs the env).
2. Put secrets in `~/.evolve_env` (`chmod 600`): `OPENROUTER_API_KEY=...`.
3. `sbatch --array=0-$((N-1)) arm.sbatch /path/to/spec.json` -- one array task per arm; a task that
   finds `store.json` in its output dir resumes (preemption-safe, like Modal).
4. Results: `$SCRATCH/evolve-results/<out>/{summary.json, partial_summary.json, store.json, run.log}`;
   rsync them back with `rsync -az sherlock:$SCRATCH/evolve-results/ <local>/`.
Cheap tasks only for now: AHC039 needs the ale-bench toolchain (Apptainer) and in-job evaluation.

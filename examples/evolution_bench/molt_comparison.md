# Molt vs known methods

Claim 1 of the Molt paper plan: Molt beats the known LLM-driven program-search methods under the
same model, budget and evaluator. Planned before the runs started (2026-11-08); the analysis below is
fixed in advance and is not changed after looking at results.

## Method under test

Molt frozen at commit b011425b (planning during probation on, `max_sprouts=4`; see
`molt_ablations.md` for how each setting was chosen). Nothing in Molt is tuned on the held-out tasks.

## Methods

| arm | what it is | runner |
|---|---|---|
| `molt` | this work | `run_bench.py --method molt` |
| `simpletes` | the base method Molt extends | `run_bench.py --method simpletes` |
| `openevolve` | open re-implementation of AlphaEvolve | `run_upstream_other.py --optimizer openevolve` |
| `shinka` | ShinkaEvolve | `run_upstream_other.py --optimizer shinka` |
| `aide` | AIDE (tree search over code drafts) | `run_upstream_other.py --optimizer aide` |

CORAL is reported separately, not in this table: it spends ~70-80x the prompt tokens per evaluation
and runs local tests the others cannot, so neither an evaluation-matched nor a dollar-matched run is
fair to it. The same-money question is answered by Molt with CORAL's budget, as before.

## Tasks

- Held-out (primary): AHC070, AHC071, AHC073 -- the three most recent short batch AtCoder Heuristic
  Contests at the time (AHC072 and AHC068 skipped because their statements restrict AI-agent
  iteration). Molt was never run on them before this comparison.
- Development (secondary): AHC058, AHC046, AHC024 -- Molt's rules were derived on these.

Test cases: the official generators, seeds 0-149 shown to every method during search; seeds
1000-1149 (held-out cases) used only for the final scores.

## Protocol

- Seeds 0-4 per (task, method): 6 x 5 x 5 = 150 runs.
- Same for every run: model `deepseek/deepseek-v4.1-flash` with reasoning off (Molt's planner calls
  use reasoning "high" and are booked against the same budget), 400 evaluations, $4 LLM cap, 2000
  LLM calls, the same whole-file starting program (`hoff/seed_<task>_whole.cpp`).
- Eval servers: seeds (0,1), (2,3), (4) of a task share a server (`main<task><a|b|c>`), so server
  drift hits every method of a seed alike. Servers avoid node sh04-05n13, where load caused
  intermittent time-outs in the ablation re-measure.
- Specs: `sherlock/specs/main.json` (written by `$EVO/make_main.py`); results in
  `results/main/<task>_<method>_s<seed>/`.

## Analysis (fixed in advance)

1. Final score of a run: its top-3 distinct programs by recorded score are re-read 3 times each,
   interleaved, on one public-case server per task; the best mean is the run's pick (any failed
   read disqualifies that program). The pick is then read 3 times on the held-out cases, on a
   server with 15 CPUs; that held-out mean is the run's final score.
2. Per task: mean +/- sd per method; Welch t-test of Molt vs each method, Holm-corrected over the
   four comparisons.
3. Primary endpoint, pooled over the three held-out tasks: each final score normalised by its task
   ((score - start) / (best score any run of the task reached - start)), paired by (task, seed);
   Wilcoxon signed-rank of Molt vs each method, Holm-corrected. The same over all six tasks is
   reported as secondary.
4. Tasks where runs either leave a local optimum or stay on it (AHC024 so far): Fisher's exact test
   on the number of runs that left it, in addition to 2.
5. Cost: LLM dollars and evaluations used per run, per method.

## Status

- 2026-11-08: tasks added (commit 93d3fe9e), spec written, 18 eval servers and the 150-run array
  submitted.

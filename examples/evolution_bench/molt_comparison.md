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
- 2026-11-08, 12.5 h in (~47 % of all evaluations done): each eval server measures one program at a
  time, and at its throughput (AHC024 ~45 s, AHC070 ~50 s per evaluation) the servers `main024a`,
  `main024b` and `main070b` would not finish 10 runs x 400 evaluations within the 48 h job limit
  (~50-54 h needed). `main024a` and `main070b` were replaced in place: a load balancer
  (`sherlock/eval_lb.py`) took over their host:port, run inside a neighbouring eval-server job on the
  same node, and sends each request to the first free one of 3 (AHC024) or 2 (AHC070) new eval
  servers (node sh04-05n13, warm-ups 2.458-2.476 vs 2.449-2.482 on the old servers), one measurement
  at a time per server. Requests are not routed by method, so every method of those seeds sees the
  same pool. `main024b` and `main070a` could not be replaced (no neighbouring job on their nodes; the
  `normal` partition ignores `--nodelist` and `--exclude`) and keep their own server. The switch cost
  one evaluation of one OpenEvolve run per replaced server ("Remote end closed connection").
  First hour: the new servers take 77-88 s per AHC024/AHC070 evaluation (old servers ~45-50 s), so
  throughput rose 80 -> 118 per hour (AHC024 a) and 74 -> 84 per hour (AHC070 b). Scores agree: an
  AHC070 program read 25.3733 on a new server and 25.3733 on an old one, and four programs that read
  0 on the new servers also read 0 on an old one.
- Found at the same time: on the busiest servers a queued request can wait longer than a client's
  time-out, and a timed-out request counts against the run's 400 evaluations. OpenEvolve (900 s)
  lost 33/799 (AHC024), 26/769 (AHC070) and 9/1136 (AHC071) evaluations up to this point. Molt and
  SimpleTES (900 s, three tries) lost about as many -- counted on 2026-11-09 as failed measurements
  that took >= 2600 s: AHC024 59/1336 and 59/1341, AHC070 81/1168 and 80/1166, AHC071 11 and 11,
  AHC073 9 and 8, none on AHC058/AHC046 -- and each cost that worker ~45 min. None for Shinka
  (1800 s) or AIDE. Reported with the results; the analysis is unchanged.
- 2026-11-09, 24.5 h in: AIDE reaches the $4 LLM cap before 400 evaluations (11 runs stopped there,
  at 121-352 evaluations) and then hangs instead of exiting; those runs were stopped by hand (SIGTERM
  to the AIDE process; the runner wrote their summaries as usual). The budget cap is part of the
  protocol, so these runs stand as they are. A 12th (AHC046 seed 0, 215 evaluations) was stopped the
  same way at 27 h; its runner then hit an I/O error writing `summary.json`, which was written by hand
  from the run's own `evals.jsonl` and `llm_calls.jsonl` (marked with a `note` field).
- 2026-11-09, rule added before any final score was computed: `main024b` (AHC024 seeds 2-3) and
  `main070a` (AHC070 seeds 0-1) measure ~63-68 programs per hour and cannot be relieved, so their
  runs will be stopped by the time limits (48 h job limit; 44 h wall clock for the upstream
  optimizers) at roughly 250-330 evaluations. For every (task, seed) whose runs were stopped by time:
  N = the fewest evaluations reached by any time-stopped run of that (task, seed); every method's run
  of that (task, seed) is cut at its first N evaluations (by evaluation time; the start program's
  read not counted), and the run's pick is chosen from the programs scored in that prefix, then
  re-measured as in step 1. Runs that stopped on the dollar cap (AIDE) before N are not cut and do
  not set N. The uncut results (each run as it stopped) are reported alongside.
- 2026-11-09, ~11:55-12:30: the scratch file system returned I/O errors (EIO) to several jobs.
  Shinka AHC070 seed 1 lost its database writes and died (Bus error) at 191 evaluations (no
  evaluation after 11:53); AIDE AHC070 seed 2 died writing its journal at 99 evaluations ($2.87
  spent) and was stopped by hand. Their `summary.json` files were written by hand from the runs' own
  logs (`note` field). Decided with the user before any final score was computed: a run stopped by
  such a failure counts as time-stopped in the cut rule above, so AHC070 seed 1 is cut at N = 191 for
  every method; AIDE runs keep their exception (they never set N), so AIDE AHC070 seed 2 stands at
  99 evaluations. At 13:45 the same error hit OpenEvolve AHC071 seed 0 after its 400 iterations (353
  evaluations; the last iterations were refused by the dollar cap) while it wrote its final
  checkpoint; its `summary.json` was also written by hand. All four I/O failures were on sh02 nodes.
  The result files of every unfinished run were checked and parse (Molt and SimpleTES write
  `store.json` atomically). Separately, AIDE AHC071 seed 3 hung at 03:49 after AIDE itself failed to parse a
  malformed function-call reply from the model (103 evaluations, $2.29); it was found 9 h later
  and stopped by hand. That is a failure of the method, so the run stands as it is.
- 2026-11-09 14:08: a 4th server for the AHC070 b balancer (node sh03-01n58, warm-up 2.450) was
  added by handing the port to a new balancer; it read an AHC070 program at 32.8636, the same as the
  old server had recorded. A third and a fourth server for AHC070 b have been on the balancer since
  10:20 and 14:08.
- An AIDE run (AHC046 seed 3) was killed at 35 evaluations by the 8 GB memory limit and restarted
  from scratch with 16 GB; the killed run is kept as `ahc046_aide_s3.oom8g`.

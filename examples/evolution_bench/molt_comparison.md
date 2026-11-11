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
  old server had recorded. A fifth (sh03-01n51, warm-up 2.459, the same program again 32.8636) was
  added the same way at 15:32.
- 2026-11-09: AIDE AHC046 seed 2 stopped by hand at the dollar cap (as above).
  Shinka AHC070 seed 2 hung in uninterruptible scratch I/O on sh02-01n12 from 13:50 (226
  evaluations); its job was cancelled at ~15:40 and its `summary.json` written by hand. Decided with
  the user before any final score was computed, by the rule above: AHC070 seed 2 is cut at N = 226
  for every method (the other methods would have reached 400). AIDE AHC070 seed 0
  stopped working at 13:02 when AIDE's own code-execution child process failed to start (102
  evaluations); found 2.5 h later and stopped by hand. It stands as it is, like every AIDE run.
  AIDE AHC024 seed 1 hung at 14:39 on the same malformed function-call reply as AHC071 seed 3 (211
  evaluations, $3.17); stopped by hand at 16:45, stands as it is. At 17:15 three more AIDE runs were
  stopped at the dollar cap: AHC024, AHC071 and AHC073 seed 0 (295, 273 and 208 evaluations).
- 2026-11-10: all 150 runs ended. Once the other runs had finished, the slow servers had fewer
  clients, and every Molt and SimpleTES run reached its 400 evaluations (the last at ~41 h), as did
  every OpenEvolve and Shinka run that did not crash (none reached the 44 h wall clock; those under
  400 recorded evaluations stopped at the dollar cap or on iterations that produced no evaluation).
  So no run was stopped by time, and the cut rule applies only to the two seeds of the crashed Shinka
  runs: AHC070 seed 1 (N = 191) and seed 2 (N = 226). AIDE: 16 runs stopped at the dollar cap, 7 at
  the 44 h wall clock, 2 at 400 evaluations, 4 on AIDE failures (2 malformed function-call replies,
  2 code-execution child processes that failed to start; one of these, AHC070 seed 3 at 104
  evaluations, exited by itself at 12:00 on 2026-11-09, during the I/O errors), 1 on the I/O error.
- 2026-11-11: step 1 started, one 16-CPU job per task on `normal` (`portfolio/main_measure.sbatch`:
  public server -> `main_measure.py`, = `abl_measure.py` plus the cut; then held-out server ->
  `holdout_measure.py`); jobs in `$EVO/main_measure_jobs.txt`.
- 2026-11-11, step 1 redone on other hardware, decided with the user before any statistic was
  computed: the first attempt's jobs all landed on old Intel nodes (Broadwell E5-2640v4, Xeon 5118),
  slower than most servers the runs were scored on (AMD EPYC 7543 and 7502; some Xeon 5118, one
  EPYC 9754). There some programs that passed during their run exceeded the CPU-time limit on every
  case and read 0 (e.g. SimpleTES AHC046 seed 1's best: "150/150 cases rejected -- TLE CPU limit
  exceeded" on a 5118, recorded 1.4532 during the run; the next program on the same server read
  exactly its recorded 1.4529), leaving five runs of four methods without a pick (Molt AHC071 s1
  and AHC073 s1, SimpleTES and OpenEvolve AHC046, AIDE AHC073 s0). Rule added: step 1 runs on the
  fastest CPU among the run servers (EPYC 7543, `--constraint=CPU_SKU:7543`; the others were Xeon
  5118, EPYC 7502, and Zen 4c parts of similar single-core speed: the EPYC 8224P of AHC058 seeds
  0-1 and the EPYC 9754 that served part of AHC024 a and AHC070 b), so no program fails only
  because the measuring machine is slower than the one that scored it. All six tasks were
  re-measured from scratch; the first attempt is kept in `results/main/intel_attempt/`.
- An AIDE run (AHC046 seed 3) was killed at 35 evaluations by the 8 GB memory limit and restarted
  from scratch with 16 GB; the killed run is kept as `ahc046_aide_s3.oom8g`.
- 2026-11-11: step 1 done on EPYC 7543 (six jobs, 1.1-2.7 h each; every warm-up read the start
  program's known score). No program of Molt, SimpleTES, OpenEvolve or Shinka failed more than one of
  its reads, and every pick read cleanly on the held-out cases. The one run left without a pick is
  AIDE AHC073 seed 0 (see Results).

## Results

Final score = held-out mean of the run's pick (`$EVO/portfolio/main_stats.py`; full output in
`results/main/stats.txt` and `stats.json`). Two cases the plan did not cover, set before the scores
were read: a run with no pick (all of its top-3 failed the public re-read) is scored as its start
program, and a failed held-out read would count as 0 in the pick's mean (none occurred).

Per task, held-out score, mean +/- sd over seeds 0-4; Holm-corrected Welch p of Molt vs that method
in brackets (bold: < 0.05).

| task | Molt | SimpleTES | OpenEvolve | Shinka | AIDE |
|---|---|---|---|---|---|
| AHC070 (cut) | **35.78** +/- 3.68 | 32.82 +/- 2.62 (0.28) | 27.84 +/- 4.53 (0.051) | 24.91 +/- 4.80 (**0.018**) | 32.74 +/- 0.89 (0.28) |
| AHC071 | **1.0584** +/- 0.0021 | 1.0572 +/- 0.0030 (0.48) | 1.0501 +/- 0.0025 (**0.0015**) | 1.0336 +/- 0.0350 (0.38) | 1.0490 +/- 0.0023 (**0.0006**) |
| AHC073 | **5.74** +/- 0.57 | 4.21 +/- 1.85 (0.14) | 1.91 +/- 0.91 (**0.0005**) | 2.32 +/- 1.67 (**0.016**) | 1.61 +/- 1.06 (**0.0007**) |
| AHC058 | **5.514** +/- 0.038 | 5.277 +/- 0.091 (**0.0025**) | 5.222 +/- 0.036 (**6e-6**) | 5.219 +/- 0.038 (**6e-6**) | 5.246 +/- 0.070 (**0.0005**) |
| AHC046 | 1.4431 +/- 0.0070 | **1.4450** +/- 0.0086 (0.72) | 1.3964 +/- 0.0219 (**0.027**) | 1.3916 +/- 0.0345 (0.054) | 1.3750 +/- 0.0332 (**0.027**) |
| AHC024 | **1.087** +/- 0.634 | 0.631 +/- 0.001 (0.73) | 0.631 +/- 0.001 (0.73) | 0.631 +/- 0.001 (0.73) | 0.898 +/- 0.596 (0.73) |

Start programs (held-out): AHC070 0.1515, AHC071 0.4974, AHC073 0.1510, AHC058 4.8777, AHC046 1.1244,
AHC024 0.6299.

Primary endpoint, pooled normalised score over the three held-out tasks (15 (task, seed) pairs;
Wilcoxon signed-rank, Holm over four comparisons):

| Molt vs | mean normalised (other) | Molt wins | median diff | Holm p |
|---|---|---|---|---|
| SimpleTES | 0.823 | 8/15 | +0.004 | 0.21 |
| OpenEvolve | 0.651 | 15/15 | +0.193 | **0.0002** |
| Shinka | 0.640 | 15/15 | +0.217 | **0.0002** |
| AIDE | 0.674 | 14/15 | +0.082 | **0.0017** |

(Molt 0.933.) Secondary, all six tasks (30 pairs): Molt 0.841; vs SimpleTES 18/30, Holm p =
**0.009**; vs OpenEvolve 29/30, vs Shinka 29/30 (both p = 4e-8); vs AIDE 27/30 (p = 3e-5).

- Molt beats OpenEvolve, Shinka and AIDE on the held-out tasks (all 15 pairs vs OpenEvolve and
  Shinka, 14/15 vs AIDE). Against
  SimpleTES, its base method, the held-out difference is not significant: Molt is ahead on AHC070
  (+3.0) and AHC073 (+1.5; SimpleTES seeds 0 and 2 stay below 2.7) and level on AHC071. Over all
  six tasks it is ahead of SimpleTES (p = 0.009), mainly from AHC058, where every Molt seed is above
  every SimpleTES seed.
- AHC024 (step 4): runs that left the local optimum (final > 1.0, the optimum being ~0.63): Molt 2/5
  (seeds 2 and 4), AIDE 1/5 (seed 0), others 0/5; Fisher p = 0.44 vs each of the three, 1.0 vs AIDE
  (Holm 1.0). Not significant with five seeds.
- Uncut (each run as it stopped; differs only on AHC070 seeds 1-2): OpenEvolve's AHC070 seed 1
  rises from 23.3 to 36.9 with its evaluations after 191, so Molt vs OpenEvolve on AHC070 drops to
  p = 0.34; the primary endpoint is unchanged in direction (vs SimpleTES p = 0.33, OpenEvolve
  0.0026, Shinka 0.0002, AIDE 0.0026).
- Cost (step 5), mean over 30 runs: Molt $2.81 (2.23-3.57) and 400 evaluations; SimpleTES $2.85,
  400 (one run, AHC071 seed 0, 374); OpenEvolve $3.07, 392; Shinka $2.80, 375 (two
  crashed on the I/O errors); AIDE $3.73, 246 (16 runs reached the $4 cap first).
- AIDE AHC073 seed 0 has no pick: its top-3 programs (recorded 5.6335) read 0 in all 9 public and
  3 held-out reads on EPYC 7543 (also on the Intel nodes). During the run the same program read
  5.6335 eight times and 0 once, on a 7543 server. A separate read on another 7543 gave 0 ("1/150
  cases rejected -- case 51: TLE CPU limit exceeded") and then 5.6335: the program runs at the edge
  of the time limit on one case, so it fails some reads on any machine. Scored as its start program
  (0.1510) by the rule above; at 5.63 AIDE's AHC073 mean would be 2.7, and no conclusion above changes.
- Time-outs (step 6): no run was stopped by time except AIDE (7 at the 44 h wall clock); the cut
  applied only to AHC070 seeds 1-2 (crashed Shinka runs).

# Molt ablations

Which parts of Molt earn their place. Each arm removes exactly one part; everything else -- model,
budget, seeds, eval server, prompts -- is held fixed. This is claim 3 of the Molt paper plan
(1: beats the known methods; 2: beats them on biology tasks; 3: its design is what does it).

## Method under test

Molt as committed with this file (`pantheon/evolution/methods/molt.py`): stall-triggered planning,
redesign directions written with Molt's own prompt (`MoltVariator`), protected sprout chains,
probation and retirement, echo detection, outage-safe counting. The ablation knobs default to Molt:
`Molt(redesign=True, protect=True, probation=True, trigger="stall")`.

The prompt rework (8feac841) is being checked by a separate A/B (`molt` vs `molt_v1`, results
`results/cmpp/`). If the old prompts win clearly, the Molt arms here are rerun with them.

## Arms (`run_bench.py --method ...`)

| arm | what is removed | question it answers |
|---|---|---|
| `molt` | nothing | reference |
| `molt_noredesign` | the redesign type: planners get RSI-top's integration and exploration only | does the redesign type matter, or is it RSI-top-style planning? |
| `molt_noprotect` | protection: a redesign's program joins its target's chains like an edit | does a new design need its own chain to survive? |
| `molt_noprobation` | probation and retirement: every sprout is kept, nothing is shed | does judging sprouts (and freeing their turns) matter? |
| `molt_periodic` | the stall trigger: plan at attempt 20, then every 65 attempts, stalled or not | does intervening *when stuck* matter, or just intervening? |
| `simpletes` | everything: plain SimpleTES (2 chains) | the base method |
| `simpletes4` | everything, with 4 chains | is Molt's gain just more chains? |
| `molt_overlap` | nothing removed; a stall plans even while sprouts are on probation (at most 4 on probation) -- Molt's default since 2026-11-08; the old behaviour is `molt_gated` | added 11-06: Molt's probation gate cut interventions to ~3 per 200 evaluations on AHC024 (the earlier arm: ~7 per 160) -- does planning more often recover the jumps? |

Sizing of the controls, measured on the Molt cmp runs (AHC058/024/046, 18 runs): interventions start
at attempt ~20 (18-26 per task) and are ~65 attempts apart (mean 65, median 68); runs end with 4-5
active chains. `molt_periodic` uses first=20, period=65 so it intervenes about as often as Molt;
`simpletes4` matches Molt's active chain count (its cap `max_established=4`).

## Protocol

- Tasks: AHC058, AHC046, AHC024 (the development tasks -- Molt's rules were derived on them, which
  is fine for ablations; the main comparison uses held-out tasks). A biology development task is to
  be added once the biology tasks are chosen.
- Seeds 0-4 per arm and task: 7 arms x 3 tasks x 5 seeds = 105 runs.
- Budget per run: 400 evaluations (`--max-eval-calls 400`), $4 LLM cap, `--max-llm-calls 2000`,
  `--iterations 2000`; model `openrouter/deepseek/deepseek-v4.1-flash` with reasoning off (Molt's
  planner calls use reasoning "high", booked against the same budget); 2 workers.
- Starting programs: `hoff/seed_<task>_whole.cpp` (the task seed wrapped in EVOLVE-BLOCK markers).
- Eval servers: one per (task, seed), shared by that seed's 7 arms, so server drift hits all arms
  of a seed alike (servers `abl<task>s<seed>`).
- Outage safety: `--max-generate-failures 40` stops an arm on a dead gateway; resubmitting the
  array resumes it (`--resume` is automatic when `store.json` exists).
- Specs: `sherlock/specs/ablate.json`, written by `$EVO/make_ablate.py`; results in
  `results/ablate/<task>_<arm>_s<seed>/`.

## Analysis

1. Final score per run: the run's best program re-measured on one server per task (3 interleaved
   reads; any failed read disqualifies), together with the seed program.
2. Per task: mean +/- sd per arm; Welch test of `molt` vs each ablation; runs beating the seed.
3. Pooled over tasks: each run's score normalised by its task (improvement over the seed divided by
   the best improvement any run reached), then a paired comparison per (task, seed) of `molt` vs each
   arm (Wilcoxon signed-rank).
4. Mechanism read-outs from Molt's own records (`method_report` in `summary.json`): interventions,
   sprouts opened / graduated / retired, echo rate, where the run's best came from.

Expected outcome for each part to count as earning its place: `molt` above the arm without it on the
pooled comparison, and not below it on any task.

## Cost and status

- Estimated LLM spend ~$250-300 (Molt runs cost ~$2.5 per 400 evaluations at this model's prices).
- 2026-11-06: arms and specs prepared; launched with this commit.
- 2026-11-06: `molt_overlap` added (15 more runs, same servers and seeds).
- 2026-11-08: all 120 runs done and re-measured (public cases, and held-out cases from the official
  generator with seeds 1000-1149). `molt_overlap` became Molt's default; the arm called `molt` in
  the results below is now `molt_gated`.

## Results (2026-11-08)

Held-out cases; each run's pick = best of its top-3 programs after 3 interleaved public reads.
Normalised score per task: 0 = the start program, 1 = the best run of the task. Paired tests over
the 15 (task, seed) pairs (Wilcoxon signed-rank). Spend = mean LLM $ per run.

| arm | AHC058 | AHC046 | AHC024 | pooled | AHC024 runs leaving the 0.63 optimum | $ / run |
|---|---|---|---|---|---|---|
| `molt_overlap` (now `molt`) | 0.94 | 0.97 | 0.74 | **0.883** | 4/5 | 2.4 |
| `molt_noprotect` | 0.91 | 0.96 | 0.77 | 0.880 | 5/5 | 3.4 |
| `molt_noprobation` | 0.83 | 0.95 | 0.85 | 0.875 | 5/5 | 3.1 |
| `molt` (now `molt_gated`) | 0.86 | 0.96 | 0.65 | 0.821 | 4/5 | 2.3 |
| `molt_periodic` | 0.88 | 0.96 | 0.60 | 0.813 | 4/5 | 2.4 |
| `molt_noredesign` | 0.71 | 0.97 | 0.64 | 0.770 | 5/5 | 3.5 |
| `simpletes4` | 0.61 | 0.93 | 0.00 | 0.514 | 0/5 | 2.1 |
| `simpletes` | 0.54 | 0.90 | 0.00 | 0.479 | 0/5 | 2.4 |

Raw held-out means: AHC058 `molt_overlap` 5.574, `molt_gated` 5.511, `simpletes` 5.278 (start
4.878); AHC046 all Molt arms 1.438-1.447, `simpletes` 1.422 (start 1.124); AHC024 start 0.630.

- Molt's machinery as a whole: `molt_gated` beats `simpletes` on 13/15 pairs (p=0.002) and
  `simpletes4` on 12/15 (p=0.003); `molt_overlap` beats `simpletes4` on 13/15 (p=0.0004). On
  AHC024 the six Molt arms left the local optimum in 27 of 30 runs, the two SimpleTES arms in 0 of
  10 (Fisher p=3e-7). More chains alone (`simpletes4`) does not do it.
- Single parts, against `molt_gated`: none is significant with 5 seeds. `noredesign` -0.05 (p=0.56;
  the loss is on AHC058, 0.71 vs 0.86); `noprotect` +0.06 and `noprobation` +0.05 (n.s.), but both
  also drop the probation gate, so they intervene ~3x as often and cost 35-50 % more per run
  (`noredesign` hit the $4 cap on AHC024 at 355-393 evaluations). What protection and probation buy
  here is the same score for about a third less money, not a higher score.
- Planning during probation: `molt_overlap` vs `molt_gated` +0.06 (10/15 pairs, p=0.15) at the same
  cost; vs `molt_noprobation` 12/15 (p=0.03), vs `molt_periodic` p=0.055. Not worse anywhere, so it
  became the default.
- AHC046 does not separate the arms; the public re-measure there had intermittent zero reads on a
  loaded node (14/121 programs, none on the held-out server), so its held-out reads are the ones used.

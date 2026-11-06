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

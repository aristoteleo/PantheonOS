# The AHC039 ablation campaign

2026-08-12. Forty-two 30-item runs on AHC039, all on Modal (`modal_exp.py`, one container per
run, results on the `evolve-exp-results` volume under `wave1/`, `wave2/`), model
`openai/gpt-5.6-luna` throughout — the same model as every number in the Erdős findings. Total
LLM spend for the campaign: ~$19.

AHC039 was chosen because Erdős cannot discriminate (`run_bench.py` header): here the eval noise
is 0.0018 against improvement steps of 0.01–0.1, the seed is a strong incumbent (5th place,
~2.47 after normalisation), and gains stay additive across 150 cases.

**Reading discipline.** Containers differ in CPU throughput and AHC scoring is wall-clock-bound,
so raw bests carry a per-container offset; every comparison below uses `gain = best − seed_own`
(the seed re-measured on that arm's own container) and the per-arm children census
(n / improved / wrecked, where "wrecked" = a valid child that lost > 0.5 — the incumbent's
structure did not survive the edit). One arm, `e3_fixedT_s2`, had a broken container: the seed
itself measured 0.0. It is excluded wherever it would mislead. n = 3 (5 for methods) — effect
directions, not significance.

## The headline: on a mature incumbent, editing beats implementing

| method (n seeds) | gain mean [range] | children improved / total | wrecked | infeasible |
|---|---|---|---|---|
| `agent_map_elites` (5) | **+0.0153** [+0.0044, +0.0220] | **40 / 113 (35%)** | 28 (25%) | 3/150 |
| `annealed` llm (3+2)   | +0.0026 [0, +0.0132] | 4 / 67 (6%) | 56 (84%) | 12/150 |
| `simpletes` (5)        | +0.0000 [0, 0] | **0 / 189** | **186 (98%)** | 28/150 |

Every MAP-Elites seed found real improvements. SimpleTES — a blind completion rewriting a
924-line C++ file per candidate — never produced a single improving child in 189 attempts.
AnnealedIdeaCode sits between, and its failure is specific enough to be interesting:

**The idea→implement pattern wrecks a strong seed.** "Implement this approach, not the
improvement you prefer" makes the agent restructure a tuned contest solution; the result
compiles, runs, and loses 1–2.5 points. On Erdős (a small numpy file, weak seed) the same
pattern was survivable; FINDINGS there even refuted "cross-idea transplants force risky
rewrites" — on THIS task the wreck rate is the whole story.

## E1 — the judge ablation (the re-run of the void `results_ablation/`)

| judge (3 seeds each) | gain mean | runs improved | children improved / total |
|---|---|---|---|
| `random` | **+0.0073** | 2/3 | **9 / 56** |
| `constant` | +0.0037 | 1/3 | 2 / 47 |
| `llm` | +0.0000 | **0/3** | **0 / 39** |

The inversion is consistent across every seed: the LLM judge's opinion is at best worthless and
plausibly harmful here. Mechanism, from the stores: with an informative-sounding judge the
selection concentrates on a few highly-rated "grand" ideas whose implementations are rewrites;
random scores spread the implementation budget across more (and more modest) ideas. The judge's
own training signal confirms there was nothing to learn: every labelled pair's gain was −1 to
−2.5 (all disasters), Spearman ≈ −0.06 — ranking wreckage is not a skill.

## E2 — calibration and the judge-state chains

Cal-off (`n_min=999`, 3 seeds): +0.0024 [0, +0.0047] vs llm baseline +0.0000 — no measurable
within-run effect, consistent with the Erdős replay (the calibration only ever changes a
handful of predictions).

The chains are the first live `--judge-state` evidence:

| chain | r1 | r2 | r3 |
|---|---|---|---|
| A (seeds 10–12): children improved | 0/12 | 1/11 | **6/12** |
| A: gain | +0.000 | +0.0025 | **+0.0147** |
| B (seeds 20–22): children improved | 6/15 | 2/9 | 5/16 |
| B: gain | +0.0131 | +0.0112 | +0.0051 |

Chain A improves monotonically as the judge's training set accumulates; chain B started strong
and stayed decent. Both chains beat the un-chained llm baseline (0 improving children in 3
runs). Suggestive of accumulation helping — but with fresh seeds per stage and n = 2 chains,
seed luck is not excluded.

## E3 — the schedule ablations (each vs the llm baseline's 0/39)

| variant | gain mean | children improved / total | note |
|---|---|---|---|
| `--norm absolute` | +0.0048 | 8 / 52 | best annealed variant; the min-max degeneracy is real |
| fixed mix (`gamma=0`) | +0.0049 | 2 / 37 | |
| fixed T (`t0=t1=0.35`) | ~0 | 4 / 27 | s2 excluded (broken container) |
| no bonus (`beta0=0`) | +0.0000 | **0 / 47, 100% wrecked** | pure exploitation = pure rewriting |

`beta0=0` is the cleanest mechanism confirmation in the campaign: remove exploration and the
method spends everything implementing its judged-best ideas — and every single implementation
wrecked the incumbent. The exploration bonus was the only thing diluting the judge's bad advice.

## What this changes

1. **The annealed method needs an edit-shaped implement step** before it can compete on strong
   seeds: implementations should start from the incumbent and be constrained to incremental
   edits (the MAP-Elites operator's framing), with the idea as guidance rather than as a spec
   to rebuild from. This is a variator/prompt change, not a search change.
2. The judge should be re-ablated AFTER (1): today it ranks wreckage. Whether it can rank
   *edits* is the question that matters, and this campaign could not ask it.
3. `--norm absolute` deserves to be the default candidate for the next round.
4. `--judge-state` chains earn a proper experiment: same-seed chained-vs-fresh, 3+ chains.

## Caveats

Single model (`gpt-5.6-luna`), single task, 30 items/run, n = 3–5. The E1 inversion and the E4
ordering are consistent across all seeds, which is the strongest statement these n support.
One container produced a dead-seed measurement (`e3_fixedT_s2`) — visible only because the
analysis re-measures the seed per container; treat any future gain > 0.1 as a probe of the
harness, not of the search.

Reproduce: `modal run modal_exp.py --spec specs/wave1.json` (and `wave2`), collect with
`--collect wave1`, analyse with `analyze_waves.py`.

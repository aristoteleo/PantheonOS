# Wave5: the three methods on deepseek-v4-flash — and the accounting bugs it exposed

2026-08-31. AHC039, `~deepseek/deepseek-v4-flash-latest` via OpenRouter, 3 seeds x 3 methods,
30 items, uniform `--max-inner-evals 4`, SimpleTES under the authors' verbatim prompt
(`UpstreamCompletionVariator`), true low-fidelity screens, both spend ledgers on. Figures:
`compare_ahc039_wave5_dsflash.png`, `budget_{llm,eval,box}_wave5.png` (budget axes are
time-share estimates; exact `usage_timeline` starts with the next wave).

## Corrected results (store truth; official points per case; child scores are FIRST reads)

| arm | seed | best child | gain | distinct children | seed re-measures |
|---|---|---|---|---|---|
| hypbandit s0/s1/s2 | 3710 / 3671 / 3700 | 3720 / 3689 / 3705 | +10 / +18 / +5 | 4 / 2 / 2 | 0 / 1 / 2 |
| mapelites s0/s1/s2 | 3705 / 3720 / 3691 | 3701 / 3723 / 3688 | −4 / +3 / −3 | 1 / 3 / 2 | 4 / 11 / 14 |
| simpletes s0/s1/s2 | 3706 / 3713 / 3678 | 3679 / 3720 / 3688 | −27 / +7 / +10 | 38 / 28 / 23 | 0 / 1 / 0 |

Identical-code re-measurements (the seed rows) spread ~±15 points, so single-run gains of
that order are noise. What survives:

- **No method reliably improves this seed on deepseek within 30 items.** All means sit within
  the re-measurement spread. The wave4 (gpt-5.6-luna) ordering does not transfer.
- **HypothesisBandit is the only 3/3-positive method** (+5..+18), a weak but consistent signal
  — from very few distinct implementations per run.
- **The model changes what the operators even DO.** MAP-Elites' deepseek agents produced 1-3
  distinct programs per run and resubmitted byte-identical code 4-14 times (the dedup store
  books those as re-measurements of the seed); its apparent curve peaks were lucky re-reads of
  unchanged code. SimpleTES's completion path, by contrast, produced 23-38 genuinely distinct
  programs per run — deepseek is a productive rewriter and a poor multi-turn agent here,
  the mirror image of gpt-5.6-luna.
- **Spend** (per run): SimpleTES ~64 LLM calls / ~2M prompt tokens; HypothesisBandit 230-310 /
  7-10M; MAP-Elites 391-564 / 15-20M for the same nothing. On per-call or per-token axes the
  rewrite paradigm dominates this model.

## The three accounting defects wave5 exposed (all fixed in run_bench)

1. **The seed's measurement fires no "measured" event**, so `seed_combined_score` (read from
   `history[0]`) was actually the FIRST CHILD. Every "depressed seed" (3431/3492/0) reported
   mid-wave was a wrecked first child mislabeled as the seed; the real seeds were all normal
   (3671-3720), and the cold-start-container theory built on them was wrong.
2. **`best` read individuals' LATEST measurement** (via ranking + `metrics()`), so on a
   wall-clock task a program's reported score depended on when it was last re-read.
3. **Dedup re-measurements masqueraded as progress**: agents resubmitting unchanged code
   attach new measurements to the same individual, and event-log-based curves rendered those
   re-reads as new bests.

Summaries now derive seed/best from the store (first valid full read per individual,
`best_child_combined_score` and `seed_remeasures` reported separately), history rows carry the
individual id, and `rebuild_from_stores.py` back-fills any wave recorded under the old
accounting.

## Still open

Measurement hygiene for wall-clock tasks: a discarded warm-up evaluation, serializing official
measurements against agent tool activity (`workers=2` lets one mutation's compile steal CPU
from another's 2-second-limit measurement), and periodic incumbent re-measurement to re-anchor
ΔR-style comparisons. Container speed calibration would make scores comparable across arms.

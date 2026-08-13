# PantheonEvo vs AgentMapElites vs SimpleTES — three tasks, one harness

2026-08-13. The pantheon_evolve_2 design document's core, implemented as
`pantheon.evolution.methods.PantheonEvo` (structured hypothesis archive, component-tagged edit
mutations with observational credit, a bandit controller over live hypotheses, staged
multi-fidelity promotion) and compared against AgentMapElites and SimpleTES on Erdős, circle
packing and AHC039 — same harness (`run_bench.py` on Modal), same model
(`openai/gpt-5.6-luna`), same 30-item budget, seeds matched. AHC039 baselines reuse the
campaign's arms (MAP-Elites from `wave2/`, SimpleTES under both operator fixes from
`wave3b/`); everything else is `wave4/`. Figures: `compare_{erdos,circle_packing,ahc039}.png`.

## Final bests

| task | PantheonEvo | AgentMapElites | SimpleTES |
|---|---|---|---|
| AHC039 (↑, gain over own seed) | **+0.0131 / 0 / +0.0137** (2/3) | +0.0044..+0.0220 (5/5) | **0 / 0 / 0** |
| circle packing (↑, Σr) | 2.6311 / 2.6310 | 2.6328 (n=2 pending) | **2.6360 / 2.6360** |
| Erdős (↓, Ψ) | 0.3810 / 0.3813 | 0.3812 / 0.3824 | 0.3810 / 0.3809 |

## What the three tasks each say

**AHC039 — the discriminating benchmark — is the result.** PantheonEvo's mean final best
(2.4775) matches AgentMapElites' (2.4763) and its best-so-far curve rises FASTEST in the first
~10 measured programs — while spending part of its 30-item budget on hypotheses, i.e. it
reaches parity with fewer program evaluations. This is where AnnealedIdeaCode — the previous
idea-driven method — found nothing in 3 runs and wrecked 84% of its implementations. The two
design changes that separate PantheonEvo from annealed are exactly the campaign's two lessons:
the implement step EDITS the incumbent (change only what the hypothesis targets) instead of
rebuilding from an idea, and hypothesis value is measured evidence instead of an LLM judge's
opinion. Idea-level search is not the problem; rewrite-shaped implementation and
opinion-shaped selection were.

**Circle packing rewards the rewrite paradigm.** SimpleTES hit the 2.635983 record on both
seeds — a small, self-contained numerical program is the best case for writing a whole fresh
file per candidate — with PantheonEvo and MAP-Elites ~0.004 behind. Consistent with the
SimpleTES paper's own circle-packing strength, and a useful reminder that no paradigm
dominates every task shape.

**Erdős still cannot discriminate.** All six arms landed in the Ψ ≈ 0.381 basin, most within
the first two measured programs (the agent writes a numerical optimiser immediately). Same
conclusion as the twenty-run study in `evolution_erdos_min_overlap/FINDINGS.md`.

## The SimpleTES-on-AHC039 erratum, reopened and properly closed

Three defects in our port surfaced in sequence, each caught by refusing to accept the number:
the prompt truncated the 43KB seed at 24KB (fixed, wave3), the operator sent no output budget
where upstream sets 32768 (fixed, wave3b) — and wave3b's children were still complete SHORT
programs (6–19KB, almost all compiling), which ruled out truncation and pointed at the real
difference: **upstream SimpleTES does not regenerate whole files. Its generation protocol is
EVOLVE-BLOCK** (`simpletes/utils/code_extract.py`): the seed marks a region, the model produces
only that block, and the program is reconstructed as EXACT_PREFIX + block + EXACT_SUFFIX with
the fixed parts kept verbatim. Whole-file mode is only the fallback for marker-less seeds. Our
"0/3, paradigm limit" claim was therefore about our port's paradigm, not SimpleTES's.

`CompletionVariator` now implements the block protocol (marker detection, upstream's generation
prompt, prefix/suffix-preserving merge), the AHC039 seed marks its simulated-annealing core
(lines 441–874; the KD-tree/scoring/IO scaffolding is fixed), and `wave3c/` re-ran the arms
under it. The protocol verifiably worked — all 56 children preserve the markers and the fixed
scaffolding, sizes 20–34KB — **and the result held: 0/3 runs improved, 29–30 of 30 children
wrecked per run.** The best block-protocol child reached 2.01 against a ~2.47 seed. The claim
that survives three rounds of harness correction is narrow and real: single-shot regeneration
of a large (24KB) heavily tuned block, blind to execution, does not preserve incumbent quality
within this budget — an agent that runs the evaluator before submitting does. One authoring
caveat: the block's SCOPE is the task author's choice; we marked the whole SA core, and a
smaller block (say, only the parameter schedule) might behave differently. Upstream published
no AHC-class results to compare against. Erdos and circle packing are unaffected: their seeds
are marker-less, where whole-file mode IS upstream's behaviour — and the packing record stood.

## Caveats

n = 2–3 per cell (5 for the reused MAP-Elites arms), one model, 30 items. The AHC039 ordering
(PantheonEvo ≈ MAP-Elites > SimpleTES) is consistent across seeds; the packing ordering
(SimpleTES first) likewise. Erdős says nothing and is reported to say nothing. PantheonEvo's
stress/transfer evaluators and cross-task memory — the design document's mechanisms 4 and 5 —
are NOT implemented (no data substrate on these tasks); this comparison tests the hypothesis
archive + component credit + adaptive controller core only.

Reproduce: `modal run --detach modal_exp.py --spec specs/wave4_compare.json`, collect with
`--collect wave4`, plot with `plot_compare.py`.

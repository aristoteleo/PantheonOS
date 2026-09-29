# Pantheon Evolve — results across all benchmarks

One page over every problem the evolution machinery has been run on, with the headline number,
where the full write-up lives, and what the result does and does not claim. Detail always beats
this page: follow the links.

| problem | metric | seed | **ours (best)** | reference | status |
|---|---|---|---|---|---|
| Circle packing n=26 (↑) | Σ radii | 1.8045 | **2.635983** | AlphaEvolve V2 2.635983 · SimpleTES 2.635983 | three-way tie |
| Erdős min overlap (↓) | Ψ | 0.5 | **0.380909** | AlphaEvolve 0.380924 · TogetherAI 0.380871 · SimpleTES 0.380868 | beats AlphaEvolve; SimpleTES & TogetherAI lower |
| Mouse-heart gene panel (↑) | FFP | 0.688 (DE) | **0.778** | best published bench row 0.738 | beats the bench leader |
| Breast-cancer panel, direct (↑) | quality | 0.5433 / 0.5880 | **0.5477 / 0.5928** | — | mechanism validated; lift small |
| AHC039 purse-seine (↑) | official pts/case | ~3703 (5th place) | 3730 (+33/case best gain) | SimpleTES paper 3783 (official SOTA) | ablation campaign benchmark |

## Circle packing, n = 26

Pack 26 circles in the unit square, maximise the sum of radii. Evolved from a naive ring layout
(1.8045) to **2.635983** — an exact tie with AlphaEvolve V2 and SimpleTES, which both report
2.635983 (SimpleTES technical report, arXiv:2604.19341, benchmark table); validity re-verified
through the evaluator (no overlaps, inside the square).

**The record was reached twice, once per model, both in solver-allowed arms**: `z-ai/glm-5.2`
(the `results/` run, iteration 2 of 3) and `claude-opus-4.8` (`results_arm_S_scipy`, 6
iterations, task description explicitly allowing scipy/SLSQP). A further six Opus arms probed
exploration shape WITHOUT the solver nudge (`results_arm_*`, main checkout;
`all_arms_final.png` is the original matched-compute figure):

| arm (all opus-4.8, n=1) | shape | best Σr |
|---|---|---|
| A depth | 4 iters × 40 calls | **2.6331** |
| D deep | 12 iters | 2.6259 |
| B2 breadth+thick | 12 × 13 | 2.6229 |
| C hybrid | 8 × 20 | 2.6058 |
| B3 = B2 + warm-start | 12 × 13 | 2.5919 |
| B breadth thin | 12 × 13 | 2.5867 |

Two transferable reads: **depth beats breadth at matched compute**, and **warm-start hurt here
too** (B3 < B2) — the same attractor the Erdős ablation later isolated. The optimum's signature
— 58 tangencies, 16 wall contacts — is drawn in `problem_01_circle_packing.mp4` /
`poster_circle_packing.png`. Details: `evolution_circle_packing/results/RESULTS.md`. Caveat:
n=1 per arm.

## Erdős minimum overlap

Continuous relaxation, scored with DeepMind's exact evaluator. Best construction **Ψ = 0.380909**
(K=951), below AlphaEvolve's 0.380924; SimpleTES (0.380868) and TogetherAI (0.380871, listed as
the prior best in the SimpleTES report) are lower still. Margins are 1e-5-scale and n=1: treat
as "same club", not a ranking. Run setup, verified: model `anthropic/claude-opus-4.8`,
MAP-Elites islands=2 with diff-based agent edits, 12 iterations, warm-start OFF.

The transferable findings came from the ablations (`evolution_erdos_min_overlap/RESULTS.md` and
`FINDINGS.md`):

- **Warm-start is an attractor.** Persisting the best solution as a data file the code reloads
  gives every lineage a monotone floor and evolves the code into a thin loader; exploration
  dies. Warm-start OFF: 0.381107 → 0.380909. Now a `--no-warm-start` knob.
- **The problem cannot rank methods.** Twenty runs put annealed/idea_code/map_elites within
  0.0005 of each other with orderings that reverse between budgets; effect ≈ noise ≈ 0.0003.
  This is why later method comparisons moved to AHC039.
- **Feasibility is a function of the action budget** (11.9% infeasible at 14 calls → 2.5% at
  28, Fisher p = 0.00099), not of prompts, gates, or operator class.
- **The redesigned judge ranks** (Spearman +0.38..+0.97 across six runs, against −0.15 before),
  but its isotonic calibration has not paid for itself within a run (closer on 4 of 12 changed
  predictions); leave-one-run-out over ~35 pairs is the first evidence for `--judge-state`.

## Gene panels

**Mouse heart (panel-selection-bench):** evolving the SELECTION ALGORITHM. Evolved panel scores
**FFP 0.778** against the DE seed's 0.688 and the best published bench row's 0.738. Full LaTeX
report with figures: `evolution_panel_mouseheart/report/report.pdf`.

**Breast cancer (Janesick), direct panel evolution:** the genome is the panel itself (a gene
list), not code; the agent researches with web_search + expression analysis while the
biological-prior dimension is hidden from it. Both panel sizes improved the hidden dimension
(+0.013) without breaking the visible ones — the mechanism works; the absolute lift is small
(+0.004..+0.005). Details: `evolution_panel_direct/RESULTS.md`.

Earlier exploratory examples (`evolution_gene_panel` RL panel, `evolution_batch_correction`
harmonypy, `evolution_topact`) are setups without headline claims.

## AHC039 (AtCoder Heuristic Contest 039)

Chosen as the benchmark that can discriminate (eval noise ~2.7 official pts/case vs improvement
steps 15–150; the seed is a 5th-place solution scoring ~3703/case). The 2026-08-12 campaign — 42 runs, four experiments, wholly
on Modal — established:

- **Editing beats implementing on a mature seed**: MAP-Elites improved in 5/5 runs
  (children improved 40/113); annealed wrecked 84% of its implementations.
- **The judge ablation inverted**: random 9/56 improving children > constant 2/47 > LLM 0/39.
- **β₀=0 wrecked 47/47** — the exploration bonus is the only dilution of the judge's advice.
- **Chained judge-state** shows the first live accumulation signal (chain A: 0→1→6).
- The SimpleTES arms were invalidated by a harness bug (seed truncated at 24KB in the prompt)
  and are being re-run — see the erratum in `evolution_bench/FINDINGS_AHC039.md`.

## Where things live

- Write-ups: each project's `RESULTS.md` / `FINDINGS.md`, linked above.
- Raw runs: `results_*/` dirs (gitignored) beside each project; the AHC039 campaign on the
  Modal volume `evolve-exp-results`; key artifacts vendored (`evolution_bench/diagram/
  judge_runs.json`, `prob_data.json`).
- Explainers: `evolution_bench/diagram/` renders four method videos, three problem videos,
  three posters, and four findings figures (`build_video.py`, `make_posters.py`,
  `plot_findings.py`).

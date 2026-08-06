# Erdős Minimum Overlap — evolution results

Continuous step-function relaxation, scored with DeepMind's ground-truth `compute_upper_bound`
(so numbers are directly comparable). **Lower Psi is better.** Records: Haugland 0.380927,
AlphaEvolve 0.380924, SimpleTES 0.380868.

Model: Opus 4.8 via OpenRouter. Fitness = `combined_score = 1 - Psi` (framework maximizes).

## Results

### Warm-start ablation (2×2: external solver × warm-start) — the headline finding

| run | solver | warm-start | final Psi | improved iters |
|---|---|---|---|---|
| `results_solver`         | on  | **on**  | 0.381107 | 2/12 |
| `results_solver_nowarm`  | on  | **off** | **0.380909** | 5/12 |
| `results_bothoff`        | off | off     | 0.381401 | 11/12 |
| `results_nosolver`       | off | off     | 0.381484 | 8/12 |

- **Turning warm-start OFF (solver on) jumps 0.381107 → 0.380909 — and 0.380909 EDGES BELOW
  AlphaEvolve's 0.380924** (essentially tied with SimpleTES 0.380868, CORAL ~0.38089).
- **Warm-start caused a sticky-champion attractor.** With warm-start on the run froze after iter1
  (2/12 improvements); with it off the agent kept exploring (5–11/12) and escaped the 0.3811 local
  optimum. The mechanism: warm-start persists the produced solution as a genome data file the code
  loads + "take-best"es, giving each lineage a monotone floor (child ≥ parent) that forbids the
  downhill moves needed to leave a basin, and evolves the code toward a thin loader — so editing code
  stops moving the solution, killing exploration. MAP-Elites doesn't catch it because its behaviour
  descriptor (code complexity/diversity) is orthogonal to the solution the warm-start homogenises, and
  the warm-start rides across cells via lineage.
- **solver vs hand-written gap is tiny (~0.0005)** — unlike circle packing (~0.003–0.007). The
  min-overlap minimax over K heights is a relatively smooth optimization a hand-written
  softmax-subgradient descent handles nearly as well as scipy.

### Cross-benchmark (same framework, vs the competitors; ours = best config)

| problem | **ours (best)** | AlphaEvolve | SimpleTES | CORAL |
|---|---|---|---|---|
| Circle packing N=26 (↑) | **2.6360** | 2.635983 | 2.635983 | 2.6360 |
| Erdős min-overlap (↓)   | **0.380909** | 0.380924 | 0.380868 | ~0.38089 |

Circle packing: **matched/edged SOTA**. Erdős: **0.380909 beats AlphaEvolve, ~4e-5 short of SimpleTES**
(the best config being solver-on + warm-start-OFF). Caveats: n=1, tiny margins (1.5e-5 over
AlphaEvolve); the CORAL Erdős number is uncertain (a lower-bound result in one source).

## Agent-trajectory analysis — potential problems

Read from the per-iteration submit summaries + tool traces of both runs.

1. **Sticky-champion rut (main issue).** After iter1 reaches ~0.3811, the agent cannot escape and
   spends most remaining iterations *re-confirming the same local optimum* instead of exploring a
   different basin. In the solver run, iter9's own summary is literally *"Investigated the K=701
   warm-start shape … Verified it is a genuine minima"* — i.e. it spent a whole mutation proving it
   was stuck. 9 of 12 solver iters were "No improvement". The QD archive + inspiration pointers did
   NOT induce enough diversity to jump basins.

2. **Warm-start becomes a "polish-the-same-thing" attractor.** Once `warm_start.json` holds the
   0.3811 shape, every later generation loads it and tries to *polish* it (marginal), rather than
   constructing a fresh candidate — reducing exploration exactly when a plateau needs more. (This is
   also why the solver winner's genome is a thin loader: scipy found the shape in the scratchpad and
   warm-start carried it out; see the `--no-warm-start` flag that closes this.)

3. **Tiny fitness dynamic range weakens selection.** `combined_score = 1 - Psi` lives in ~[0.50,
   0.62]; per-iteration gains near the optimum are ~1e-4, which the archive's normalization can
   barely act on. A steeper transform (e.g. `-log(Psi - 0.379)` or `1/(Psi - lower_bound)`) would
   amplify the near-optimum gradient and likely help escape the plateau.

### Verified NON-problems (checked, and they're fine)

- **K inflation (95 → 701) is legitimate, not a discretization exploit.** For a *step* function the
  overlap integral is piecewise-linear in the shift k, so its max is attained at an integer-step
  shift — exactly what `np.correlate` (integer lags) evaluates. So the scorer is *exact* for step
  functions; a finer K just represents a better shape. And 0.381107 > the true inf ~0.3809, so it
  does not under-estimate.
- No validity gaming; both runs' winners are genuinely feasible (heights in [0,1], sum == K/2).

### Actionable

- For plateau-prone problems: (a) a steeper fitness transform; (b) periodically *ignore* warm-start
  / force fresh construction to diversify; (c) reward the agent for *different* basins, not
  re-verifying the champion. The current loop wastes budget re-confirming a stuck optimum.

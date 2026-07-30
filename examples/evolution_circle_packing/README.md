# Circle packing (N=26) — Pantheon Evolution example

Pack **26 non-overlapping circles** inside the unit square `[0,1]×[0,1]` to **maximize the
sum of their radii**. This is the classic AlphaEvolve / OpenEvolve benchmark; the state of
the art for N=26 is a sum of radii of **≈ 2.635**.

It's a good evolution benchmark because the search space is smooth but deceptive: naive
layouts are far from optimal, and progress needs both better center placement *and* radius
optimization — exactly the kind of algorithmic + implementation reasoning a coding agent can do.

## Files

- `packing.py` — the program that gets evolved. Exposes `run_packing() -> (centers, radii, sum)`;
  the seed is a naive concentric-ring layout with proportionally-shrunk radii.
- `evaluator.py` — `evaluate(workspace_path)` (AlphaEvolve/OpenEvolve rules): validates the
  packing (inside the square, no overlaps, tolerance `1e-6`) and returns
  `combined_score = sum_radii / 2.635` (0 if invalid). Fitness = `combined_score`.
- `run_evolution.py` — runner (uses `single_agent_mutation=True`, i.e. the single
  full-capability coding agent).

## Run

```bash
cd examples/evolution_circle_packing

# quick sanity check of the seed + evaluator (no LLM):
python packing.py            # prints the seed's sum_of_radii
python evaluator.py          # prints the metrics dict for the seed

# evolve (needs provider API keys in the environment, like any agent run):
python run_evolution.py --iterations 40 --workers 2 --output results/
# pin a model:
python run_evolution.py --iterations 40 --model gemini-cli/gemini-2.5-flash
```

Watch `combined_score` climb toward (and hopefully past) `1.0`. The best packing is saved to
`results/packing_best.py`.

## Metrics

`sum_radii`, `min_radius`, `max_radius`, `target_ratio`, `validity`, `combined_score`
(the fitness). An invalid packing scores 0 and reports `invalid_reason`.

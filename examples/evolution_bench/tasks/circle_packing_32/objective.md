# Circle packing, n = 32

Pack 32 non-overlapping circles in the unit square to MAXIMISE the sum of their radii.

`solution.py` must expose `construct_circles()` returning an array of shape (32, 3) with one row
(x, y, r) per circle. The evaluator validates feasibility strictly (inside the square and
non-overlapping to 1e-12) and scores `combined_score = sum of radii`. The evolved code sits
between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

Published results: human best 2.936; AlphaEvolve 2.937944; AlphaEvolve V2 / TTT-Discover
2.939572 (the current record). The seed is a plain grid. Compute is cheap; iterate numerically
inside `construct_circles()` if that helps, but stay under the evaluator's timeout.

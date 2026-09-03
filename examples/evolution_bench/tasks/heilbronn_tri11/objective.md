# Heilbronn triangle problem, equilateral region, n = 11

Place 11 points inside the equilateral triangle with vertices (0,0), (1,0), (0.5, sqrt(3)/2) to
MAXIMISE the area of the smallest triangle formed by any three of them, divided by the area of
the enclosing triangle (sqrt(3)/4).

`solution.py` must expose `heilbronn_triangle11()` returning an array of shape (11, 2). Every
point must lie inside the triangle (tolerance 1e-6); otherwise the construction is infeasible.
The evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = min_area_normalized / 0.036529889880030156` (AlphaEvolve's reported best).

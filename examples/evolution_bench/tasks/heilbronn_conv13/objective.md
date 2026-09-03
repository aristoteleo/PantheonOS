# Heilbronn triangle problem, convex region, n = 13

Place 13 points anywhere in the plane to MAXIMISE the area of the smallest triangle formed by
any three of them, divided by the area of their convex hull (so the objective is scale- and
translation-invariant). The hull must have positive area.

`solution.py` must expose `heilbronn_convex13()` returning an array of shape (13, 2). The
evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = min_area_normalized / 0.030936889034895654` (AlphaEvolve's reported best).

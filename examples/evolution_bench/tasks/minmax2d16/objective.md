# Minimising the max/min distance ratio, dimension 2, n = 16

Place 16 points in the plane to MAXIMISE (d_min / d_max)^2, the squared ratio of the smallest to
the largest pairwise distance. No boundary constraint. A perfect configuration (all distances
equal) would score 1; the best known ratio for 16 points is d_max/d_min = 12.889266112.

`solution.py` must expose `min_max_dist_dim2_16()` returning an array of shape (16, 2). The
evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = (d_min/d_max)^2 / (1/12.889266112)` (AlphaEvolve's reported best; the
benchmark constant is CodeEvolve's).

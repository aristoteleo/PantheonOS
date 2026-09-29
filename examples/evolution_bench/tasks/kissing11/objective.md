# Kissing number in dimension 11

Construct as many integer vectors in Z^11 as possible such that no vector is zero and the minimum
pairwise squared distance is at least the maximum squared norm -- after normalisation, that is a
set of unit spheres all touching a central unit sphere without overlapping. MAXIMISE the count.

`solution.py` must expose `kissing_number11()` returning an array of shape (m, 11); entries are
rounded to integers and verified exactly. A violation is infeasible (zero score). The evolved code
sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = m / 593` (AlphaEvolve's reported best; 592 was the previous record; the LP upper
bound is 2432). The seed has 2 points.

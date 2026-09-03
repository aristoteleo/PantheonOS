# Hexagon packing, n = 11

Pack 11 non-overlapping unit regular hexagons inside the smallest regular hexagon: MINIMISE the
outer side length s (the score is 1/s). Inner hexagons may be placed and rotated freely, must be
fully contained, and pairwise disjoint (tolerance 1e-6); the outer hexagon may be positioned and
rotated freely.

`solution.py` must expose `hexagon_packing_11()` returning a tuple (inner, outer, s): `inner` an
array of shape (11, 3) with rows (x, y, angle_degrees), `outer` an array (cx, cy, angle_degrees),
and `s` the outer side length. The evolved code sits between `# EVOLVE-BLOCK-START` and
`# EVOLVE-BLOCK-END`.

`combined_score = (1/s) / (1/3.930092)` (AlphaEvolve's reported best; 3.943 before).

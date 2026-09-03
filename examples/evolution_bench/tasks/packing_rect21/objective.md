# Circle packing in a rectangle of perimeter 4, n = 21

Pack 21 non-overlapping circles of any radii inside a rectangle with w + h <= 2 (perimeter <= 4)
to MAXIMISE the sum of radii. The rectangle is the minimum bounding rectangle of the circles;
overlap and containment are checked with tolerance 1e-6.

`solution.py` must expose `circle_packing21()` returning an array of shape (21, 3) with rows
(x, y, r). The evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = sum of radii / 2.3658321334167627` (AlphaEvolve's reported best; 2.364 before).

# Circle packing, n = 26

Place 26 non-overlapping circles inside the unit square [0,1] x [0,1] to MAXIMISE the sum of
their radii. The evaluator loads `solution.py`, calls `run_packing()` -> (centers, radii),
validates (inside the square, no overlaps, tolerance 1e-6) and reports
`combined_score = sum of radii` (0 if invalid).

The naive ring seed scores ~1.80; the published record is 2.635983 (AlphaEvolve V2 and
SimpleTES both report it). Real gains come from jointly optimising centre placement AND radii —
e.g. running a numerical optimiser (SLSQP or similar) with restarts inside `run_packing()` —
not from tweaking the fixed ring layout. Keep the runtime under the evaluator's timeout.

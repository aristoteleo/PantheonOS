# Second autocorrelation inequality (C2)

Construct a NON-NEGATIVE step function f on [-1/4, 1/4] to MAXIMISE

    R(f) = ||f * f||_2^2 / (||f * f||_1 * ||f * f||_inf)

which is a lower bound on the constant C2. `solution.py` must expose `run_code()` returning the
list of step heights (optionally as the first element of a tuple). The evaluator recomputes R(f)
with SimpleTES's exact discretisation (piecewise-linear L2 integral on a grid of len(f*f)+2
points, L1 as the mean over len(f*f)+1 cells); heights are clipped to [0, 1000]; a self-reported
value is ignored. The evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = R(f)` (raw). Published: previous best 0.88922; AlphaEvolve 0.8962; SimpleTES's
instruction targets R(f) > 0.97. Each evaluation must finish under the 300 s timeout.

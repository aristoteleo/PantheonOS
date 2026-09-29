# First autocorrelation inequality (C1)

Discretise [-1/4, 1/4] into n equal bins and search for a NON-NEGATIVE step function f that
MINIMISES

    C1(f) = 2 n * max(f * f) / (sum f)^2

where f * f is the unnormalised discrete autoconvolution. `solution.py` must expose `run_code()`
returning the list of step heights (optionally as the first element of a tuple). Heights are
clipped to [0, 1000] by the evaluator; any self-reported value is ignored. The evolved code sits
between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

The framework maximises, so `combined_score = 1 / (1e-8 + C1)` -- SimpleTES's own transform.
Published: AlphaEvolve C1 <= 1.5053 (previous 1.5098); ThetaEvolve and SimpleTES report further
improvements. The seed runs an LP-guided local search for 250 s; each evaluation must finish
under the 300 s timeout.

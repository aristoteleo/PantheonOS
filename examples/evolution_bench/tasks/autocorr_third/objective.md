# Third autocorrelation inequality (C3)

Construct a discrete function f: [-1/4, 1/4] -> R (heights may be NEGATIVE) that MINIMISES

    C3(f) = max |f * f| / (integral f)^2

evaluated on the uniform grid with dx = 0.5 / n. `solution.py` must expose
`construct_function()` returning a 1-D array of n heights (n is yours to choose). A self-reported
value is ignored. The evolved code sits between `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END`.

`combined_score = 1.4556427953745406 / C3`, so matching AlphaEvolve's C3 scores exactly 1.0 and
beating it scores above 1 -- SimpleTES's own transform. Each evaluation must finish under the
70 s timeout (SimpleTES's rule for this task).

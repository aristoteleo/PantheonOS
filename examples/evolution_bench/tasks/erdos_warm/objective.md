# Erdos Minimum Overlap

Construct a step function h: [0,2] -> [0,1] with unit mass (sum of the K step heights == K/2)
minimising

    Psi(h) = max over shifts k of  integral h(x) * (1 - h(x+k)) dx

The evaluator loads `solution.py`, calls `run_construction()`, validates feasibility, and
reports `combined_score = 1 - Psi` (higher is better). The uniform seed scores 0.5; published
constructions reach Psi ~ 0.3809 (AlphaEvolve 0.380924, SimpleTES 0.380868). Good shapes are
mirror-symmetric with mass pushed toward the edges in bursts and a central plateau. You may
choose K. Compute is cheap: iterate numerically inside `run_construction()` if that helps, but
keep the runtime under the evaluator's timeout.

Warm start: if a file `warm_start.json` exists in the working directory, it holds the best
construction found so far in this run, as {"sequence": [h_1, ..., h_K], "psi": <its Psi>, "K": K}.
You may load it and improve on it -- refine it, upsample it to a finer K, use it to initialise
your own optimisation -- or ignore it and construct from scratch. Whatever you return is scored
on its own merits, and if it is the best so far it becomes the next program's warm start.

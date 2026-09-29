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

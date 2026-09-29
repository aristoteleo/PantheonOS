"""The three problems' real artifacts, vendored for the explainer videos.

`prob_data.json` holds, verbatim from the runs that produced them:

  * erdos    -- the record construction (K=951 steps, Psi = 0.380909, beats AlphaEvolve's
                0.380924), its Psi(k) profile, and the uniform seed's profile for contrast.
                Re-verified against the evaluator's own `compute_upper_bound` at vendor time.
  * packing  -- the best n=26 packing (sum of radii 2.635983 vs the published 2.635), centers
                and radii as `results/packing_best.py` returns them.
  * ahc039   -- case 0 of the 150 official AHC039 cases: a 2,200-per-species sample of the
                10,000 fish (drawn seeded, for rendering only) plus the 5th-place seed
                solution's real 92-vertex net. The inside-counts (4151 mackerel, 617 sardines)
                were computed against ALL 10,000 fish before sampling.

Regenerate with the vendoring script in the session scratchpad if the artifacts move; every
number above is printed and checked at vendor time.
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "prob_data.json")) as _fh:
    _D = json.load(_fh)

ERDOS = _D["erdos"]
PACKING = _D["packing"]
AHC = _D["ahc039"]

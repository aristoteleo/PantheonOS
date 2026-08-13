"""One real HypothesisBandit run, curated for the explainer.

Source: `wave4/ahc039_pantheon_evo_s0` on the `evolve-exp-results` Modal volume -- the method's
own `method.json` state (hypothesis archive, credit table, event log) plus the run summary.
Model gpt-5.6-luna, task AHC039, seed 0. Nothing here is simulated: every hypothesis text,
every dR, every retirement and every low-fidelity rejection below happened in that run, in this
order. Mechanism texts are shortened for the screen; ids match the archive.

The run is a gift to the narrator: initialization's first child gains and its next two lose
(the bandit's turn); the bay-fill hypothesis lands the run's best program with its second child
and is retired IN THE SAME EVENT because its average is negative; and three candidates are
rejected by the 30-case screen without ever costing a 150-case measurement.
"""
from __future__ import annotations

SEED_SCORE = 2.468969
BEST_SCORE = 2.482058
FULL_CASES, SCREEN_CASES = 150, 30

# priority(h) = mu + LAM*sigma + ETA*novelty; sigma = PRIOR_SIGMA/sqrt(1+n); novelty = 1/(1+n);
# softmax at TAU. Retire when n >= 2 and mean(dR) <= 0, or after 2 screen fails.
LAM, ETA, TAU, PRIOR_SIGMA = 1.0, 0.5, 0.35, 0.05

COMPONENTS = ["initialization", "core-algorithm", "search-strategy",
              "numerical-optimization", "parameters", "output-construction"]

HYPS = {
    "3a8e7645": {"comp": "parameters", "label": "retune the cooling schedule"},
    "43e8ce52": {"comp": "initialization", "label": "seed from 3 best rectangles"},
    "7916fd53": {"comp": "output-construction", "label": "fill rectangular bays"},
    "de72dd2f": {"comp": "core-algorithm", "label": "exact sweep-line rescorer"},
    "1567b728": {"comp": "search-strategy", "label": "ruin & recreate 10-25% arcs"},
    "ec37d56d": {"comp": "numerical-optimization", "label": "line search on one edge"},
    "d0793db2": {"comp": "numerical-optimization", "label": "prefix-sum score grid"},
}

# The featured card of act one, lightly shortened from the archive's verbatim text.
CARD = {
    "id": "43e8ce52",
    "target_component": "initialization",
    "mechanism": "start from the 3 highest-scoring disjoint rectangles,\n"
                 "joined into one polygon by thin corridors",
    "expected_effect": "search begins around several positive clusters at once —\n"
                       "which one bounding box cannot represent",
    "falsification": "no significant gain over the current initialization,\n"
                     "or corridors consistently degrade the seeds → wrong",
}

EVENTS = [
    {"kind": "hyp", "id": "3a8e7645"},
    {"kind": "hyp", "id": "43e8ce52"},
    {"kind": "hyp", "id": "7916fd53"},
    {"kind": "hyp", "id": "de72dd2f"},
    {"kind": "measure", "hyp": "43e8ce52", "dR": +0.010951, "score": 2.479920, "base": 2.468969},
    {"kind": "measure", "hyp": "3a8e7645", "dR": +0.005982, "score": 2.474951, "base": 2.468969},
    {"kind": "measure", "hyp": "7916fd53", "dR": -0.007849, "score": 2.472071, "base": 2.479920},
    {"kind": "measure", "hyp": "7916fd53", "dR": +0.002138, "score": 2.482058, "base": 2.479920,
     "best": True},
    {"kind": "measure", "hyp": "de72dd2f", "dR": -0.023840, "score": 2.456080, "base": 2.479920},
    {"kind": "measure", "hyp": "de72dd2f", "dR": -0.001493, "score": 2.480564, "base": 2.482058},
    {"kind": "hyp", "id": "1567b728"},
    {"kind": "measure", "hyp": "43e8ce52", "dR": -0.016609, "score": 2.465449, "base": 2.482058},
    {"kind": "hyp", "id": "ec37d56d"},
    {"kind": "measure", "hyp": "43e8ce52", "dR": -0.012387, "score": 2.469671, "base": 2.482058,
     "inflight": True},
    {"kind": "measure", "hyp": "1567b728", "dR": -0.012062, "score": 2.470000, "base": 2.482058},
    {"kind": "screen", "hyp": "ec37d56d", "score": 1.168431, "base": 2.4821},
    {"kind": "screen", "hyp": "3a8e7645", "score": 2.459729, "base": 2.4821},
    {"kind": "screen", "hyp": "ec37d56d", "score": 1.800147, "base": 2.4821},
    {"kind": "hyp", "id": "d0793db2"},
]

# wave4, all three AHC039 seeds -- for the closing card.
WAVE4 = {"hypothesis_bandit": {"mean": 2.4775, "measured": "8-13"},
         "agent_map_elites": {"mean": 2.4763, "measured": "~28"}}

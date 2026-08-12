"""The judge's learning, taken from real runs rather than a simulation.

Every other number in these videos comes from a stubbed run. This one does not: the six
`AnnealedIdeaCode` runs on the Erdos minimum-overlap problem each kept the judge's training log in
`method.json` -> `state.history`, one record per labelled pair, in the order it arrived, with the
base the work item pinned. That is what the calibration actually saw.

Replaying it through the real `Calibration` gives the state the judge was in at every step. The
rule is strictly one-step-ahead -- each pair is predicted using only the pairs before it -- so the
comparison between the raw prediction and the calibrated one is honest about what the fit knew.

What the replay says, and it is not the flattering answer:

  * as a RANKER the judge works. Spearman between prediction and outcome runs +0.38..+0.97 per
    run, against -0.15 for the judge this one replaced.
  * as a LEARNER, within a single run, the calibration has not paid for itself. It only ever holds
    five to ten pairs, it changed 12 of 44 predictions, and it was closer on 4 of those 12.
  * given a training set -- five runs' pairs, tested on the sixth -- it starts to help on the mean
    (0.0434 -> 0.0401) by damping large optimistic misses, while still being worse on more points
    than it fixes. `--judge-state` is the knob that would do this across runs and has never been
    exercised.

None of these n are significant. They are reported because the video shows the mechanism, and the
mechanism should not be shown claiming more than the runs support.

The pairs are vendored in `judge_runs.json`. The run directories they came from are gitignored, so
reading them live would break this module -- and the video build that imports it -- on any clean
checkout. `python judge_data.py --extract` regenerates the file wherever those runs do exist.
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
from typing import Any, Dict, List, Tuple

from pantheon.evolution.variators.judge import Calibration, _spearman

HERE = os.path.dirname(os.path.abspath(__file__))
VENDORED = os.path.join(HERE, "judge_runs.json")
RUNS_AT = os.path.normpath(os.path.join(HERE, "..", "..", "evolution_erdos_min_overlap"))

N_MIN, PRIOR_SIGMA = 5, 0.15
"""The judge's defaults, and what these runs used."""

FEATURED = "results_compare/annealed_s1"
"""The longest training log of the six, and the run FINDINGS quotes at Spearman +0.49."""


def extract() -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Straight off disk, for regenerating the vendored copy."""
    out = []
    for p in sorted(glob.glob(os.path.join(RUNS_AT, "results_*/**/method.json"), recursive=True)):
        h = (json.load(open(p)).get("state") or {}).get("history") or []
        # Other methods keep a history of a different shape; only this one logs judge pairs.
        if h and "raw" in h[0] and "gain" in h[0] and len(h) >= N_MIN:
            out.append((os.path.relpath(os.path.dirname(p), RUNS_AT),
                        [{k: r[k] for k in ("raw", "base", "gain", "score", "t")} for r in h]))
    return out


HISTORIES = [(r["run"], r["pairs"]) for r in json.load(open(VENDORED))["runs"]]


def replay(history):
    """The judge's state at every step, plus the fit it arrived at afterwards."""
    cal = Calibration(n_min=N_MIN, prior_sigma=PRIOR_SIGMA)
    steps = []
    for rec in history:
        before = cal(rec["raw"])
        steps.append({
            "n": len(cal), "fitted": cal.fitted, "said": rec["raw"], "gained": rec["gain"],
            "before": before, "sigma": cal.sigma,
            "knots_before": (list(cal._kx), list(cal._ky)),
        })
        cal.observe(rec["raw"], rec["gain"])
        cal.fit()
        steps[-1]["knots_after"] = (list(cal._kx), list(cal._ky))
        steps[-1]["sigma_after"] = cal.sigma
        steps[-1]["fitted_after"] = cal.fitted
    return steps, cal


STEPS, FINAL = replay(dict(HISTORIES)[FEATURED])
FEATURED_RHO = _spearman([s["said"] for s in STEPS], [s["gained"] for s in STEPS])

SUMMARY = [(tag, len(h), _spearman([r["raw"] for r in h], [r["gain"] for r in h]))
           for tag, h in HISTORIES]


def _tally():
    raw_e, cal_e, changed = [], [], []
    for _, h in HISTORIES:
        for s in replay(h)[0]:
            r, c = abs(s["said"] - s["gained"]), abs(s["before"] - s["gained"])
            raw_e.append(r)
            cal_e.append(c)
            if abs(s["before"] - s["said"]) > 1e-9:
                changed.append((r, c))
    n, m = len(raw_e), len(changed)
    better = sum(1 for r, c in changed if c < r)
    p = (min(1.0, sum(math.comb(m, i) for i in range(min(better, m - better) + 1)) / 2 ** m * 2)
         if m else float("nan"))
    return {"n": n, "raw_mae": sum(raw_e) / n, "cal_mae": sum(cal_e) / n,
            "changed": m, "better": better, "p": p}


TALLY = _tally()


def _cross_run():
    """Leave-one-run-out: fit on the other five runs' pairs, predict this one's."""
    raw_e, cal_e = [], []
    for tag, held in HISTORIES:
        cal = Calibration(n_min=N_MIN, prior_sigma=PRIOR_SIGMA)
        for other, h in HISTORIES:
            if other != tag:
                for r in h:
                    cal.observe(r["raw"], r["gain"])
        cal.fit()
        raw_e += [abs(r["raw"] - r["gain"]) for r in held]
        cal_e += [abs(cal(r["raw"]) - r["gain"]) for r in held]
    n = len(raw_e)
    return {"n": n, "raw_mae": sum(raw_e) / n, "cal_mae": sum(cal_e) / n}


CROSS = _cross_run()


if __name__ == "__main__":
    if "--extract" in sys.argv:
        runs = extract()
        json.dump({"source": "examples/evolution_erdos_min_overlap/results_*/*/method.json"
                             " -> state.history",
                   "note": "The judge training pairs from six real AnnealedIdeaCode runs, in the"
                           " order the judge saw them. The run directories themselves are"
                           " gitignored; these are vendored so the video and the numbers in"
                           " FINDINGS.md stay reproducible.",
                   "runs": [{"run": t, "pairs": p} for t, p in runs]},
                  open(VENDORED, "w"), indent=1)
        print(f"wrote {VENDORED}: {len(runs)} runs, {sum(len(p) for _, p in runs)} pairs")
        raise SystemExit(0)

    for tag, n, rho in SUMMARY:
        print(f"{tag:30} n={n:3}  spearman {rho:+.2f}")
    print(f"\nwithin a run, one step ahead: n={TALLY['n']}  raw MAE {TALLY['raw_mae']:.4f}  "
          f"calibrated {TALLY['cal_mae']:.4f}  "
          f"(changed {TALLY['changed']}, closer on {TALLY['better']}, p={TALLY['p']:.2f})")
    print(f"across runs, leave-one-out:   n={CROSS['n']}  raw MAE {CROSS['raw_mae']:.4f}  "
          f"calibrated {CROSS['cal_mae']:.4f}")
    print(f"\nfeatured: {FEATURED}  rho {FEATURED_RHO:+.2f}")
    for i, s in enumerate(STEPS, 1):
        print(f"  {i:2} n={s['n']} {'fitted  ' if s['fitted'] else 'identity'} "
              f"said {s['said']:+.4f} -> {s['before']:+.4f}   gained {s['gained']:+.4f}")

#!/usr/bin/env python
"""A controlled comparison of three search policies on Erdos minimum-overlap.

The point of the design is to make a difference in score attributable to something. So everything
that is not the policy is held fixed:

  operator     every arm writes programs with the same coding agent. `idea_code` normally uses a
               blind completion and `annealed` normally uses an agent, so comparing them at their
               defaults would confound the policy change with the operator change -- and the
               operator was already measured to matter more.
  warm start   OFF everywhere. It has been measured to cause a sticky-champion collapse on this
               problem, and the best result on record (Psi 0.380909) was produced with it off.
  model        one model, one budget, one evaluator, one seed set.

  A  annealed    new policy: one scale, soft annealed selection, learned judge, inheritance
  B  idea_code   old policy: top-k cutoff over a judge's [0,1] prior, rigid phase alternation
  C  map_elites  the established baseline that produced the recorded result

A vs B isolates the policy redesign. A vs C says whether any of it beats what was already there.

Nothing here interprets the numbers -- two seeds on one problem cannot settle much, and the run
budget is far below what the recorded results used.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent.resolve()
PY = sys.executable

ARMS = {
    "annealed":  ["--method", "annealed"],
    "idea_code": ["--method", "idea_code", "--code-variator", "agent"],
    "map_elites": ["--method", "map_elites"],
}


def one(arm: str, seed: int, a) -> dict:
    out = HERE / a.output / f"{arm}_s{seed}"
    sm = out / "summary.json"
    if sm.exists() and not a.force:
        # A finished run is evidence that cost hours to produce; re-running it to fill a gap in a
        # later seed would throw that away and, worse, quietly replace a recorded number with a
        # differently-seeded one.
        s = json.loads(sm.read_text())
        print(f"[skip ] {arm} seed={seed}  already done, psi={s.get('best_psi')}", flush=True)
        row = {"arm": arm, "seed": seed, "rc": 0, "seconds": s.get("seconds", 0.0)}
        row.update({k: s.get(k) for k in
                    ("best_psi", "best_combined_score", "items_run", "failures",
                     "individuals", "variator", "operator")})
        return row
    out.mkdir(parents=True, exist_ok=True)
    log = out.with_suffix(".log")
    cmd = [PY, "-u", str(HERE / "run_v2.py"), *ARMS[arm],
           "--iterations", str(a.iterations), "--workers", str(a.workers),
           "--seed", str(seed), "--model", a.model,
           "--no-warm-start", "--output", str(out),
           "--tool-budget", str(a.tool_budget)]
    t0 = time.time()
    print(f"[start] {arm} seed={seed}  -> {log.name}", flush=True)
    with open(log, "w") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    row = {"arm": arm, "seed": seed, "rc": rc, "seconds": round(dt, 1)}
    sm = out / "summary.json"
    if sm.exists():
        s = json.loads(sm.read_text())
        row.update({k: s.get(k) for k in
                    ("best_psi", "best_combined_score", "items_run", "failures",
                     "individuals", "variator", "operator")})
    print(f"[done ] {arm} seed={seed}  rc={rc}  {dt/60:.1f}min  "
          f"psi={row.get('best_psi')}", flush=True)
    return row


def main(a) -> None:
    jobs = [(arm, s) for s in range(a.seeds) for arm in ARMS]
    print(f"{len(jobs)} runs, {a.iterations} items each, {a.parallel} at a time, "
          f"model={a.model}", flush=True)
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        rows = list(pool.map(lambda j: one(j[0], j[1], a), jobs))

    (HERE / a.output / "compare.json").write_text(json.dumps(rows, indent=1))

    # Refuse to present a comparison whose arms were not given the same operator allowance. The
    # first version of this experiment ran one arm with an unlimited action budget and the other
    # two on 14, because a method's `default_variator` dropped the knob; the resulting difference
    # in feasibility and wall clock looked like a search result and was a budget result. Checked
    # rather than trusted, because that failure was invisible in every number the run printed.
    ops = {}
    for r in rows:
        op = r.get("operator")
        if op:
            ops.setdefault(json.dumps(op, sort_keys=True), []).append(f"{r['arm']}_s{r['seed']}")
    if len(ops) > 1:
        print("\n" + "!" * 70)
        print("ARMS ARE NOT COMPARABLE -- their operators were given different allowances:")
        for cfg, who in ops.items():
            print(f"  {', '.join(who)}\n    {cfg}")
        print("Fix the mismatch and re-run; the numbers below do not isolate the policy.")
        print("!" * 70)

    print("\n" + "=" * 70)
    print(f"{'arm':12} {'seed':>4} {'Psi':>10} {'items':>6} {'fail':>5} {'min':>6}")
    for r in sorted(rows, key=lambda r: (r["arm"], r["seed"])):
        psi = r.get("best_psi")
        print(f"{r['arm']:12} {r['seed']:>4} "
              f"{(f'{psi:.6f}' if isinstance(psi, float) else '-'):>10} "
              f"{str(r.get('items_run', '-')):>6} {str(r.get('failures', '-')):>5} "
              f"{r['seconds']/60:>6.1f}")
    print("-" * 70)
    for arm in ARMS:
        got = [r["best_psi"] for r in rows
               if r["arm"] == arm and isinstance(r.get("best_psi"), float)]
        if got:
            print(f"{arm:12} best {min(got):.6f}   mean {sum(got)/len(got):.6f}   n={len(got)}")
    print("=" * 70)
    print(f"-> {HERE / a.output / 'compare.json'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--iterations", type=int, default=40)
    p.add_argument("--seeds", type=int, default=2)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--parallel", type=int, default=2, help="runs in flight at once")
    p.add_argument("--model", default="openai/gpt-5.6-luna")
    p.add_argument("--output", default="results_compare")
    p.add_argument("--tool-budget", type=int, default=28,
                   help="action budget per mutation, held equal across arms. Feasibility tracked "
                        "this closely: at 1.11 evaluator calls per program 18.9% of submissions "
                        "violated the constraints, at 2.25 only 6.3% did")
    p.add_argument("--force", action="store_true",
                   help="re-run arms that already have a summary.json")
    main(p.parse_args())

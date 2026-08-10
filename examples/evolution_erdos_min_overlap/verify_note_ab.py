#!/usr/bin/env python
"""Does one sentence change how often the coding agent submits an infeasible program?

Three arms of `compare_v2.py` shared a coding agent and produced infeasible programs at very
different rates:

    annealed    3/54 =  5.6%
    map_elites  8/66 = 12%     (no "implement this approach" pressure at all)
    idea_code  17/48 = 35%     Fisher p=0.00015 against annealed, 0.005 against map_elites

The only arm carrying a feasibility reminder in its per-item instruction was the lowest, and the
arm with no approach-following pressure sat in the middle. Two explanations were checked against
the stored runs first and both failed: the agent's system prompt already says NEVER SUBMIT AN
INVALID SOLUTION, so the reminder is not new information; and only 1 of 24 idea_code mutations
started from an infeasible parent, so it is not a cascade.

What is left is that a reminder next to the task is worth more than the same instruction in a long
system prompt. This runs the toggle.

    treatment   idea_code + the sentence, seeds 0 and 1
    control     the existing idea_code runs in results_compare, byte-identical configuration
                apart from the flag

Reusing the earlier runs as the control is what makes this two runs rather than four. It is only
legitimate because nothing else changed -- same code, same model, same budget, same seeds.
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


def infeasible(store_path: Path):
    inds = json.loads(store_path.read_text())["individuals"]
    feas = infeas = 0
    for i in inds:
        if i["kind"] != "code":
            continue
        for m in reversed(i["measurements"]):
            if "combined_score" in m["metrics"]:
                v = m["metrics"].get("validity")
                if v is not None and v <= 0:
                    infeas += 1
                else:
                    feas += 1
                break
    return infeas, feas + infeas


def one(seed: int, a) -> dict:
    out = HERE / a.output / f"verify_s{seed}"
    if (out / "summary.json").exists() and not a.force:
        print(f"[skip ] seed={seed} already done", flush=True)
    else:
        out.mkdir(parents=True, exist_ok=True)
        cmd = [PY, "-u", str(HERE / "run_v2.py"),
               "--method", "idea_code", "--code-variator", "agent", "--verify-note",
               "--iterations", str(a.iterations), "--workers", str(a.workers),
               "--seed", str(seed), "--model", a.model,
               "--no-warm-start", "--output", str(out)]
        t0 = time.time()
        print(f"[start] verify seed={seed}", flush=True)
        with open(out.with_suffix(".log"), "w") as fh:
            subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT)
        print(f"[done ] verify seed={seed}  {(time.time()-t0)/60:.1f}min", flush=True)
    i, t = infeasible(out / "store.json")
    s = json.loads((out / "summary.json").read_text())
    return {"seed": seed, "infeasible": i, "programs": t, "psi": s["best_psi"]}


def main(a) -> None:
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        treat = list(pool.map(lambda s: one(s, a), range(a.seeds)))

    ctrl = []
    for s in range(a.seeds):
        p = HERE / "results_compare" / f"idea_code_s{s}" / "store.json"
        if p.exists():
            i, t = infeasible(p)
            ctrl.append({"seed": s, "infeasible": i, "programs": t})

    ti, tt = sum(r["infeasible"] for r in treat), sum(r["programs"] for r in treat)
    ci, ct = sum(r["infeasible"] for r in ctrl), sum(r["programs"] for r in ctrl)
    print("\n" + "=" * 62)
    print(f"{'condition':22} {'infeasible':>12} {'rate':>8}")
    print(f"{'control (no sentence)':22} {ci:>5}/{ct:<6} {ci/max(1,ct):>7.1%}")
    print(f"{'treatment (sentence)':22} {ti:>5}/{tt:<6} {ti/max(1,tt):>7.1%}")
    try:
        from scipy.stats import fisher_exact
        _, p = fisher_exact([[ci, ct - ci], [ti, tt - ti]])
        print(f"{'Fisher exact':22} {'p =':>12} {p:.5f}")
    except ImportError:
        pass
    print("-" * 62)
    for r in treat:
        print(f"  treatment seed={r['seed']}  {r['infeasible']}/{r['programs']} "
              f"infeasible   Psi={r['psi']:.6f}")
    print("=" * 62)
    (HERE / a.output / "verify_note_ab.json").write_text(
        json.dumps({"treatment": treat, "control": ctrl}, indent=1))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--iterations", type=int, default=40)
    p.add_argument("--seeds", type=int, default=2)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--parallel", type=int, default=2)
    p.add_argument("--model", default="openai/gpt-5.6-luna")
    p.add_argument("--output", default="results_verify")
    p.add_argument("--force", action="store_true")
    main(p.parse_args())

"""Steps 2-5 of the analysis fixed in molt_biology.md, from measure_bio.py's outputs.

    python stats_bio.py RESULTS_ROOT [PILOT.json] [--json=OUT]

Final score of a run = mean of its pick's test reads (measure_<task>.json). PILOT.json (pilot_bio.py)
gives the known methods' test scores for step 4.
"""
import json
import statistics as S
import sys
from pathlib import Path

from scipy import stats

args = [a for a in sys.argv[1:] if not a.startswith("--")]
root = Path(args[0])
pilot = json.loads(Path(args[1]).read_text()) if len(args) > 1 else {}
out_json = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--json=")), None)
TASKS = ["bio_integration", "bio_denoise"]
METHODS = ["molt", "simpletes", "openevolve", "shinka", "aide"]
OTHERS, SEEDS = METHODS[1:], range(5)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - k) * ps[i]))
        adj[i] = run
    return adj


res, out = {}, {"tasks": {}}
for t in TASKS:
    m = json.load(open(root / f"measure_{t}.json"))
    start, runs = m["start"]["test"], m["runs"]
    fin = {(me, s): runs[f"{t}_{me}_s{s}"]["final"] for me in METHODS for s in SEEDS}
    res[t] = (start, fin)
    print(f"\n== {t}  (start: test {start:.4f}, valid {m['start']['valid']:.4f})")
    rows = {me: [fin[(me, s)] for s in SEEDS] for me in METHODS}
    for me in METHODS:
        nopick = [f"s{s}" for s in SEEDS if runs[f"{t}_{me}_s{s}"]["pick_id"] == "start"]
        print(f"  {me:10s} {S.mean(rows[me]):.4f} +/- {S.stdev(rows[me]):.4f}   " + " ".join(f"{x:.4f}" for x in rows[me])
              + (f"   [no pick: {nopick}]" if nopick else ""))
    ps = [stats.ttest_ind(rows["molt"], rows[o], equal_var=False).pvalue for o in OTHERS]
    ps = [1.0 if p != p else p for p in ps]
    adj = holm(ps)
    for o, p, a in zip(OTHERS, ps, adj):
        print(f"  Welch molt vs {o:10s} diff {S.mean(rows['molt']) - S.mean(rows[o]):+.4f}  p={p:.4g}  Holm p={a:.4g}")
    tj = {"start": start, "scores": rows, "welch": dict(zip(OTHERS, zip(ps, adj)))}
    # step 4: against the known methods, on test
    kind = t.removeprefix("bio_")
    refs = {k.split("/")[0]: v[0] for k, v in (pilot.get(t) or {}).items()
            if k.endswith("/test") and not k.startswith(("start/", "pilot-"))}
    if refs:
        best = max(refs, key=lambda k: refs[k].get("combined_score") or 0)
        b = refs[best]["combined_score"]
        beat = {me: sum(x > b for x in rows[me]) for me in METHODS}
        print(f"  best known method on test: {best} {b:.4f}; runs above it (of 5): {beat}")
        names = list((refs[best].get("datasets") or {}).keys())
        print("  per test dataset      " + " ".join(f"{n[:12]:>12s}" for n in names))
        for k, v in refs.items():
            d = v.get("datasets") or {}
            print(f"    ref {k:14s}  " + " ".join(f"{(d.get(n) or {}).get('total', (d.get(n) or {}).get('score', 0)) or 0:12.4f}" for n in names))
        per = {}
        for me in METHODS:
            per[me] = {n: S.mean(runs[f"{t}_{me}_s{s}"]["test_datasets"].get(n, 0.0) for s in SEEDS) for n in names}
            print(f"    {me:18s}  " + " ".join(f"{per[me][n]:12.4f}" for n in names))
        tj.update({"best_ref": [best, b], "runs_above_best_ref": beat, "per_dataset": per})
    out["tasks"][t] = tj

print("\n== pooled normalised over the two tasks: (score - start) / (best run of task - start), paired by (task, seed)")
n = {}
for t, (start, fin) in res.items():
    best = max(fin.values())
    n[t] = {k: (v - start) / (best - start) if best > start else 0.0 for k, v in fin.items()}
for me in METHODS:
    v = [n[t][(me, s)] for t in TASKS for s in SEEDS]
    print(f"  {me:10s} mean {S.mean(v):.3f}  median {S.median(v):.3f}")
ps, ds = [], []
for o in OTHERS:
    d = [n[t][("molt", s)] - n[t][(o, s)] for t in TASKS for s in SEEDS]
    ds.append(d)
    ps.append(stats.wilcoxon(d).pvalue if any(d) else 1.0)
adj = holm(ps)
for o, d, p, a in zip(OTHERS, ds, ps, adj):
    print(f"  Wilcoxon molt vs {o:10s} median diff {S.median(d):+.3f}  wins {sum(x > 0 for x in d)}/{len(d)}  p={p:.4g}  Holm p={a:.4g}")
out["pooled"] = {o: {"median_diff": S.median(d), "wins": sum(x > 0 for x in d), "n": len(d), "p": p, "holm": a}
                 for o, d, p, a in zip(OTHERS, ds, ps, adj)}

print("\n== cost per method (mean over the 10 runs; min-max)")
out["cost"] = {}
for me in METHODS:
    usd, ev = [], []
    for t in TASKS:
        for s in SEEDS:
            p = root / f"{t}_{me}_s{s}" / "summary.json"
            if not p.exists():
                continue
            sm = json.load(open(p))
            usd.append((sm.get("llm_usage") or {}).get("cost_usd", 0.0))
            eu = sm.get("eval_usage") or {}
            e = eu.get("calls")
            ev.append(e - eu.get("calls_harness", 0) if e is not None else sm.get("items_run", 0) + 1)
    if usd:
        print(f"  {me:10s} ${S.mean(usd):.2f} ({min(usd):.2f}-{max(usd):.2f})   evaluations {S.mean(ev):.0f} ({min(ev)}-{max(ev)})")
        out["cost"][me] = {"usd": usd, "evals": ev}
if out_json:
    json.dump(out, open(out_json, "w"), indent=1)

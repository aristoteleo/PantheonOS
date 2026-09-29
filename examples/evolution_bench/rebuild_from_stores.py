"""Repair a wave's summaries from its stores -- the ground truth the events mislabeled.

    uv run python rebuild_from_stores.py --pairs SUMMARY:STORE ... --out-dir DIR

wave5 shipped with three accounting defects (since fixed in run_bench): the seed's measurement
fires no event, so `seed_combined_score` was really the first CHILD; `best` read individuals'
LATEST measurement; and dedup booked re-measurements of unchanged genomes as if they were new
programs. This script rewrites each summary's score fields and history from the store:

  - seed = the parentless individual's FIRST valid full measurement
  - best = max valid full measurement over all individuals; best_child excludes the seed
  - history = each NON-SEED individual's first valid full measurement, in store insertion
    order (creation order), with timestamps recovered by matching scores against the old
    event log where unambiguous (else interpolated by index)

Corrected summaries go to --out-dir under the same basenames; a collected-format json
(`corrected_collected.json`) is emitted beside them for plot_compare.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def valid_full(v):
    return [m["metrics"]["combined_score"] for m in v.get("measurements") or []
            if m.get("ok") and m.get("fidelity", "full") == "full"
            and (m.get("metrics", {}).get("validity", 1) or 0) > 0
            and m.get("metrics", {}).get("combined_score") is not None]


def rebuild(summary_path: str, store_path: str, out_dir: Path):
    r = json.load(open(summary_path))
    st = json.load(open(store_path))
    inds = st["individuals"]
    items = list(inds.items()) if isinstance(inds, dict) else [(str(i), v) for i, v in
                                                               enumerate(inds)]
    seed_key, seed_ms = None, []
    series = []                      # (key, first_valid_full) in insertion order, seed excluded
    for k, v in items:
        ms = valid_full(v)
        if not ms:
            continue
        if not v.get("parent_ids") and seed_key is None:
            seed_key, seed_ms = k, ms
        else:
            series.append((k, ms[0]))

    old_events = [h for h in (r.get("history") or []) if h.get("score") is not None]
    used = set()

    def t_for(score, idx):
        for j, h in enumerate(old_events):
            if j not in used and abs(h["score"] - score) < 1e-9:
                used.add(j)
                return h["t"]
        if old_events:
            frac = (idx + 1) / max(len(series), 1)
            return old_events[-1]["t"] * frac
        return 0.0

    history = [{"t": t_for(s, i), "n": i + 1, "score": s, "id": k, "valid": 1.0}
               for i, (k, s) in enumerate(series)]
    seed = seed_ms[0] if seed_ms else 0.0
    best_child = max((s for _, s in series), default=None)
    r.update({
        "seed_combined_score": seed,
        "best_combined_score": max([seed] + [s for _, s in series]) if seed_ms else best_child,
        "best_child_combined_score": best_child,
        "seed_remeasures": max(0, len(seed_ms) - 1),
        "history": history,
        "rebuilt_from_store": True,
    })
    out = out_dir / Path(summary_path).name
    json.dump(r, open(out, "w"), indent=1)
    return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True,
                    help="summary.json:store.json paths, colon-separated")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    collected = []
    for pair in a.pairs:
        sp, tp = pair.split(":")
        r = rebuild(sp, tp, out_dir)
        arm = Path(sp).stem.replace("w5_", "wave5/ahc039_")
        collected.append({"arm": arm, "method": r.get("method"), "task": r.get("task"),
                          "best": r["best_combined_score"],
                          "seed_own": r["seed_combined_score"], "history": r["history"]})
        print(f"{Path(sp).name}: seed {r['seed_combined_score']*1500:.0f}  "
              f"best {r['best_combined_score']*1500:.0f}  "
              f"child_best {(r['best_child_combined_score'] or 0)*1500:.0f}  "
              f"distinct_children {len(r['history'])}  "
              f"seed_remeasures {r['seed_remeasures']}")
    json.dump(collected, open(out_dir / "corrected_collected.json", "w"))

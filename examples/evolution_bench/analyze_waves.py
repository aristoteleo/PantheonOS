"""Read a wave's summaries off the Modal volume and lay the arms side by side.

    uv run modal run modal_exp.py --collect wave1 > /tmp/wave1.json
    uv run python analyze_waves.py /tmp/wave1.json /tmp/wave2.json

Reporting discipline, learned on Erdos: n=2 giving a complete separation was luck, and the write-
up said so only after believing it for a day. So: effect sizes with per-arm spreads, no
significance theatre at n=3, and the seed score printed beside everything because "beat the other
arm" and "beat the starting point" are different claims.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict


def group(rows):
    arms = defaultdict(list)
    for r in rows:
        name = r["arm"].split("/")[-1]
        base = name.rsplit("_s", 1)[0] if "_s" in name else name
        # chain stages group by chain+stage, not by seed: e2_chainA_r1
        arms[base].append(r)
    return arms


def main(paths):
    rows = []
    for p in paths:
        rows += json.load(open(p))
    done = [r for r in rows if r.get("best") is not None]
    print(f"{len(done)}/{len(rows)} arms have a summary\n")

    # GAIN over the arm's own seed measurement is the comparable number: containers differ in
    # CPU throughput and AHC scores are wall-clock-limited, so raw bests carry a per-container
    # offset about as large as the effects under test.
    print(f"{'arm group':24} {'n':>2}  {'gain mean':>10}  {'gain range':>22}  "
          f"{'children: n/improved/wrecked':>28}  {'fail':>9}")
    for base, rs in sorted(group(done).items()):
        gains = [r["gain"] for r in rs if r.get("gain") is not None]
        fail = sum(r.get("failures") or 0 for r in rs)
        items = sum(r.get("items") or 0 for r in rs)
        k = {"n": 0, "improved": 0, "wrecked": 0}
        for r in rs:
            for key in k:
                k[key] += (r.get("kids") or {}).get(key, 0)
        g = (f"{statistics.mean(gains):+.6f}  [{min(gains):+.6f}, {max(gains):+.6f}]"
             if gains else f"{'?':>34}")
        print(f"{base:24} {len(rs):>2}  {g}  "
              f"{k['n']:>10} /{k['improved']:>3} /{k['wrecked']:>3}  {fail:>4}/{items}")
    print("\nper-arm detail:")
    for r in sorted(done, key=lambda x: x["arm"]):
        g = f"{r['gain']:+.6f}" if r.get("gain") is not None else "      ?"
        noise = (f"  (hist_max {r['hist_max']:.6f})"
                 if r.get("hist_max") and r["hist_max"] > (r["best"] or 0) else "")
        print(f"  {r['arm']:34} gain {g}  best {r['best']:.6f}  "
              f"fail {r.get('failures')}  {r.get('seconds', 0)}s{noise}")


if __name__ == "__main__":
    main(sys.argv[1:])

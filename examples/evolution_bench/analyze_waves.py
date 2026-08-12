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

    for base, rs in sorted(group(done).items()):
        bests = [r["best"] for r in rs]
        seeds = [r["seed"] for r in rs if r.get("seed") is not None]
        fail = sum(r.get("failures") or 0 for r in rs)
        items = sum(r.get("items") or 0 for r in rs)
        spread = (max(bests) - min(bests)) if len(bests) > 1 else 0.0
        print(f"{base:24} n={len(rs)}  best {statistics.mean(bests):.6f} "
              f"[{min(bests):.6f}, {max(bests):.6f}]  spread {spread:.6f}  "
              f"failures {fail}/{items}")
    print("\nper-arm detail:")
    for r in sorted(done, key=lambda x: x["arm"]):
        print(f"  {r['arm']:34} best {r['best']:.6f}  items {r.get('items')}  "
              f"failures {r.get('failures')}  {r.get('seconds', 0)}s")


if __name__ == "__main__":
    main(sys.argv[1:])

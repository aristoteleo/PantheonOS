"""Price every arm from its token ledger -- one table, one convention, all methods.

    uv run python price_arms.py --summaries 'dir/*.json' [--prompt-price P --completion-price C]

Exists because spend was only half-recorded: the agent path priced its calls through the
adapter's registry lookup while the completion path reported $0.00 (the OpenAI-protocol
response carries no price unless the gateway is asked for usage accounting -- now it is, but
runs recorded before that fix have no cost at all). Pricing from tokens gives every arm a
number under the SAME rule, which is what a cost comparison needs.

Default prices are deepseek-v4-flash-latest on OpenRouter (2026-09-01):
$0.04998/M prompt, $0.09996/M completion.

Caveat printed with the table: the agent path books each call's context+output together as
prompt-side tokens, so its completion share is invisible and its cost here is a LOWER bound.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

PROMPT_PRICE = 0.00000004998
COMPLETION_PRICE = 0.00000009996


def method_of(name: str) -> str:
    return ("hypothesis_bandit" if "hypbandit" in name else
            "agent_map_elites" if "mapelites" in name else
            "simpletes" if "simpletes" in name else
            "lab_notebook" if "labnotebook" in name else "?")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--summaries", nargs="+", required=True)
    ap.add_argument("--prompt-price", type=float, default=PROMPT_PRICE)
    ap.add_argument("--completion-price", type=float, default=COMPLETION_PRICE)
    a = ap.parse_args()

    rows = []
    seen = set()
    for pat in a.summaries:
        for f in sorted(glob.glob(os.path.expanduser(pat))):
            base = os.path.basename(f).replace("_p.json", "").replace(".json", "")
            if base in seen or base.endswith("_store") or "collected" in base:
                continue
            try:
                r = json.load(open(f))
            except Exception:
                continue
            lu = r.get("llm_usage")
            if not lu:
                continue
            seen.add(base)
            pt = lu.get("prompt_tokens", 0) or 0
            ct = lu.get("completion_tokens", 0) or 0
            rows.append({"arm": base, "method": method_of(base), "calls": lu.get("calls", 0),
                         "prompt": pt, "completion": ct,
                         "priced": pt * a.prompt_price + ct * a.completion_price,
                         "recorded": lu.get("cost_usd", 0.0) or 0.0})

    by_m: dict = {}
    print(f"{'arm':32s} {'method':18s} {'calls':>5} {'tokens':>12} {'priced $':>9} "
          f"{'recorded $':>10}")
    for r in sorted(rows, key=lambda x: (x["method"], x["arm"])):
        toks = r["prompt"] + r["completion"]
        print(f"{r['arm']:32s} {r['method']:18s} {r['calls']:>5} {toks:>12,} "
              f"{r['priced']:>9.2f} {r['recorded']:>10.2f}")
        m = by_m.setdefault(r["method"], {"n": 0, "priced": 0.0, "tok": 0, "calls": 0})
        m["n"] += 1
        m["priced"] += r["priced"]
        m["tok"] += toks
        m["calls"] += r["calls"]

    print("\nper method (priced from tokens, one rule for all):")
    total = 0.0
    for m, v in sorted(by_m.items()):
        total += v["priced"]
        print(f"  {m:20s} n={v['n']} calls={v['calls']:>5} tokens={v['tok']:>12,} "
              f"${v['priced']:.2f}  (${v['priced'] / v['n']:.2f}/arm)")
    print(f"  {'TOTAL':20s} ${total:.2f}")
    print("\nNote: the agent path books context+output together as prompt-side tokens, so its "
          "completion share is invisible and its cost above is a LOWER bound.")

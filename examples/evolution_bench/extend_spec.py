"""Write the continuation spec for a wave whose curves have not saturated.

    uv run python extend_spec.py --spec specs/erdos-wave1.json [--factor 2]

Ceilings are CUMULATIVE across sessions (the resumed run reloads its ledgers), so session two
of a 160-call arm runs with --max-llm-calls 320 and spends only the next 160. The continuation
spec adds --resume and multiplies every ceiling by --factor; launch it exactly like the
original: modal run --detach modal_exp.py --spec <name>_continue.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CEILS = {"--max-llm-calls", "--max-llm-tokens", "--max-eval-calls"}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--factor", type=int, default=2)
    a = ap.parse_args()
    arms = json.loads(Path(a.spec).read_text())
    for arm in arms:
        argv = arm["argv"]
        for i, tok in enumerate(argv):
            if tok in CEILS:
                argv[i + 1] = str(int(argv[i + 1]) * a.factor)
        if "--resume" not in argv:
            argv.append("--resume")
    out = Path(a.spec).with_name(Path(a.spec).stem + "_continue.json")
    out.write_text(json.dumps(arms, indent=1) + "\n")
    print(f"{out} ({len(arms)} arms, ceilings x{a.factor}, --resume)")

#!/usr/bin/env python
"""Run the ORIGINAL SimpleTES engine (github.com/wq-will/SimpleTES) on a bench task.

    python run_upstream_simpletes.py --task ahc039 --generations 60 --output OUT

Exists to answer one question the port cannot: whatever our `CompletionVariator` port gets
wrong or right, what does the AUTHORS' engine do on this task, same model, same evaluator,
same marked seed? The upstream commit is pinned; the engine is cloned and pip-installed at run
time (its deps are not ours); the bench evaluator is adapted to upstream's
`evaluate(filepath) -> {'combined_score': ...}` protocol; and the checkpoint is post-processed
into our `summary.json` shape so `--collect` and `plot_compare.py` read it like any arm.

One generation prompt = one candidate in upstream's accounting, so `--generations 60` matches
our 30-item x k=2 arms in completions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
UPSTREAM_REPO = "https://github.com/wq-will/SimpleTES"
UPSTREAM_SHA = "a19a54b109db6185ab1f13dd59dd150074b24136"

ADAPTER = '''\
"""Bench-task evaluator adapted to upstream SimpleTES's protocol: evaluate(filepath)."""
import importlib.util
import os
import shutil
import tempfile

TASK_DIR = {task_dir!r}
EVOLVE_FILE = {evolve_file!r}

os.environ.setdefault("AHC_TASK_DIR", TASK_DIR)
os.environ.setdefault("TASK_DIR", TASK_DIR)
for k, v in {env!r}.items():
    os.environ.setdefault(k, str(v))

_spec = importlib.util.spec_from_file_location("bench_eval", os.path.join(TASK_DIR, "evaluator.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def evaluate(filepath):
    tmp = tempfile.mkdtemp(prefix="upstream_eval_")
    try:
        shutil.copyfile(filepath, os.path.join(tmp, EVOLVE_FILE))
        m = _mod.evaluate(tmp, "full")
        m["combined_score"] = float(m.get("combined_score", 0.0))
        return m
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--generations", type=int, default=60)
    ap.add_argument("--model", default="openai/gpt-5.6-luna")
    ap.add_argument("--seed", type=int, default=0, help="recorded only; upstream has no seed flag")
    ap.add_argument("--eval-timeout", type=int, default=900)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()

    out = Path(a.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    task_dir = HERE / "tasks" / a.task
    cfg = json.loads((task_dir / "task.json").read_text()) if (task_dir / "task.json").exists() else {}
    evolve_file = cfg.get("evolve", "solution.py")

    # ---- the engine, pinned ------------------------------------------------
    eng = out / "upstream"
    if not (eng / "main.py").exists():
        subprocess.run(["git", "clone", UPSTREAM_REPO, str(eng)], check=True)
        subprocess.run(["git", "-C", str(eng), "checkout", UPSTREAM_SHA], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "litellm>=1.80.0", "rich>=13.0.0", "questionary>=2.0.0"], check=True)

    adapter = out / "adapter_evaluator.py"
    adapter.write_text(ADAPTER.format(task_dir=str(task_dir), evolve_file=evolve_file,
                                      env=dict(cfg.get("env") or {})))

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    cmd = [sys.executable, "main.py",
           "--init-program", str(task_dir / evolve_file),
           "--evaluator", str(adapter),
           "--instruction", str(task_dir / "objective.md"),
           "--max-generations", str(a.generations),
           "--model", a.model,
           "--api-base", "https://openrouter.ai/api/v1",
           "--api-key", api_key,
           "--eval-timeout", str(a.eval_timeout),
           "--output-path", str(out / "checkpoints")]
    print("=" * 78)
    print(f"UPSTREAM SimpleTES @ {UPSTREAM_SHA[:8]} | task={a.task} | model={a.model} | "
          f"generations={a.generations}")
    print("=" * 78, flush=True)
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=eng).returncode

    # ---- checkpoint -> our summary shape ----------------------------------
    best, history = None, []
    ckroot = out / "checkpoints"
    metas = sorted(ckroot.rglob("metadata.json"), key=lambda p: p.stat().st_mtime)
    nodesf = sorted(list(ckroot.rglob("nodes.json")) + list(ckroot.rglob("nodes.json.gz")),
                    key=lambda p: p.stat().st_mtime)
    if metas:
        best = json.loads(metas[-1].read_text()).get("best_score")
    if nodesf and nodesf[-1].suffix == ".json":
        nodes = json.loads(nodesf[-1].read_text())
        rows = [(n.get("created_at") or n.get("generation") or i,
                 (n.get("metrics") or {}).get("combined_score", n.get("score")))
                for i, n in enumerate(nodes)]
        rows = [(t, s) for t, s in rows if isinstance(s, (int, float))]
        rows.sort(key=lambda r: r[0])
        history = [{"n": i + 1, "score": float(s), "valid": 1.0}
                   for i, (_, s) in enumerate(rows)]
        if best is None and history:
            best = max(h["score"] for h in history)

    json.dump({"task": a.task, "evolve": evolve_file, "method": "simpletes_upstream",
               "model": a.model, "seed": a.seed,
               "operator": {"class": "UpstreamSimpleTES", "sha": UPSTREAM_SHA},
               "search": {"engine": "upstream", "generations": a.generations,
                          "selector": "balance (upstream default)"},
               "items_run": len(history), "failures": 0,
               "best_combined_score": best if best is not None else 0.0,
               "seed_combined_score": history[0]["score"] if history else 0.0,
               "seconds": time.time() - t0, "history": history},
              open(out / "summary.json", "w"), indent=1)
    print(f"-> {out}/summary.json  best={best}  nodes={len(history)}  rc={rc}")
    sys.exit(0 if rc == 0 else rc)


if __name__ == "__main__":
    main()

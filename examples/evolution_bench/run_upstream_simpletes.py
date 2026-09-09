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
TASK_NAME = {task_name!r}
EVAL_SERVER = {eval_server!r}     # when set, evaluate on the bench's eval server, not in-process
BENCH_DIR = {bench_dir!r}
BOOT_LOG = {boot_log!r}

os.environ.setdefault("AHC_TASK_DIR", TASK_DIR)
os.environ.setdefault("TASK_DIR", TASK_DIR)
for k, v in {env!r}.items():
    os.environ.setdefault(k, str(v))

_spec = importlib.util.spec_from_file_location("bench_eval", os.path.join(TASK_DIR, "evaluator.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _evaluate_remote(filepath):
    import json, sys, time
    import httpx
    sys.path.insert(0, BENCH_DIR)
    from remote_eval import server_token
    payload = {{"token": server_token(), "task": TASK_NAME,
               "files": {{EVOLVE_FILE: open(filepath).read()}}, "fidelity": "full"}}
    body, last = None, None
    for attempt in range(3):
        try:
            r = httpx.post(EVAL_SERVER, json=payload, timeout=900.0, follow_redirects=True)
            r.raise_for_status(); body = r.json(); break
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(5 * (attempt + 1))
    if body is None:
        return {{"combined_score": 0.0, "validity": 0.0, "error": f"eval server unreachable: {{last}}"}}
    if body.get("boot_id"):
        with open(BOOT_LOG, "a") as fh:
            fh.write(json.dumps({{"t": time.time(), "boot_id": body["boot_id"]}}) + "\\n")
    m = dict(body.get("metrics") or {{}})
    m["combined_score"] = float(m.get("combined_score", 0.0) or 0.0)
    return m


def evaluate(filepath):
    if EVAL_SERVER:
        return _evaluate_remote(filepath)
    tmp = tempfile.mkdtemp(prefix="upstream_eval_")
    try:
        shutil.copyfile(filepath, os.path.join(tmp, EVOLVE_FILE))
        import inspect
        params = inspect.signature(_mod.evaluate).parameters
        m = _mod.evaluate(tmp, "full") if len(params) >= 2 else _mod.evaluate(tmp)   # fidelity only where accepted
        m["combined_score"] = float(m.get("combined_score", 0.0))
        return m
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
'''


USAGE_HOOK = '''\
"""Book every litellm completion's token usage to SIMPLETES_USAGE_LOG (rejected generations
included -- the checkpoint keeps usage only for evaluated nodes). Installed through a .pth
file so every process of this interpreter -- the engine's spawned LLM workers included --
wraps `litellm.completion` before the engine binds it. Synchronous on purpose: litellm's own
success callbacks run on a thread that a short-lived worker may not wait for."""
import functools, json, os, time

LOG = os.environ.get("SIMPLETES_USAGE_LOG")
if LOG:
    try:
        import litellm
        _orig = litellm.completion

        @functools.wraps(_orig)
        def completion(*args, **kwargs):
            t0 = time.time()
            resp = _orig(*args, **kwargs)
            try:
                u = getattr(resp, "usage", None)
                rec = {"t": time.time(), "pid": os.getpid(), "seconds": round(time.time() - t0, 1),
                       "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                       "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                       "n": kwargs.get("n")}
                with open(LOG, "a") as fh:
                    fh.write(json.dumps(rec) + "\\n")
            except Exception:
                pass
            return resp

        litellm.completion = completion
    except Exception:
        pass
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--generations", type=int, default=60)
    ap.add_argument("--model", default="openai/gpt-5.6-luna")
    ap.add_argument("--seed", type=int, default=0, help="recorded only; upstream has no seed flag")
    ap.add_argument("--eval-timeout", type=int, default=900)
    ap.add_argument("--output", required=True)
    # engine knobs, passed through verbatim; defaults mirror our port's bench configuration
    ap.add_argument("--num-chains", type=int, default=2)
    ap.add_argument("--k-candidates", type=int, default=2)
    ap.add_argument("--num-inspirations", type=int, default=2)
    ap.add_argument("--selector", default="rpucg")
    ap.add_argument("--reflection", action="store_true", help="keep upstream's reflection calls (off = as our port)")
    ap.add_argument("--max-tokens", type=int, default=64000)
    ap.add_argument("--max-total-tokens", type=int, default=3500000)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--gen-concurrency", type=int, default=2)
    ap.add_argument("--eval-concurrency", type=int, default=2)
    ap.add_argument("--eval-server", default=os.environ.get("EVAL_SERVER_URL", ""),
                    help="bench eval server URL (AHC039); evaluations then run there, like our arms")
    ap.add_argument("--init-eval-repeats", type=int, default=1, help="upstream defaults to 16; ours measures the seed once")
    ap.add_argument("--no-stream-k", action="store_true", help="upstream's --no-stream-k-candidates")
    ap.add_argument("--llm-timeout", type=int, default=900, help="per-request timeout passed to the engine (its default is 3000 s)")
    ap.add_argument("--llm-retry", type=int, default=1)
    ap.add_argument("--checkpoint-interval", type=int, default=5,
                    help="engine checkpoint every N evaluations (its --log-interval), so a preempted arm can resume")
    ap.add_argument("--engine-python", default=None,
                    help="interpreter to run the engine with (its own venv); skips the pip install")
    a = ap.parse_args()

    out = Path(a.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    task_dir = HERE / "tasks" / a.task
    cfg = json.loads((task_dir / "task.json").read_text()) if (task_dir / "task.json").exists() else {}
    evolve_file = cfg.get("evolve", "solution.py")

    # ---- the engine, pinned ------------------------------------------------
    # /tmp, not the output volume: a git clone is thousands of small files and network-volume
    # writes make it crawl. Only the checkpoints and summary live on the volume.
    eng = Path(os.environ.get("UPSTREAM_ENGINE_DIR", "/tmp/upstream_simpletes"))
    if not (eng / "main.py").exists():
        subprocess.run(["git", "clone", UPSTREAM_REPO, str(eng)], check=True)
        subprocess.run(["git", "-C", str(eng), "checkout", UPSTREAM_SHA], check=True)
    py = a.engine_python or sys.executable
    if not a.engine_python:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        "litellm>=1.80.0", "rich>=13.0.0", "questionary>=2.0.0",
                        "pandas", "numpy", "scikit-learn", "psutil", "matplotlib",
                        "protobuf", "cython"], check=True)

    adapter = out / "adapter_evaluator.py"
    boot_log = out / "eval_server_boots.jsonl"
    adapter.write_text(ADAPTER.format(task_dir=str(task_dir), evolve_file=evolve_file,
                                      task_name=a.task, eval_server=a.eval_server or "",
                                      bench_dir=str(HERE), boot_log=str(boot_log),
                                      env=dict(cfg.get("env") or {})))

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    usage_log = out / "llm_usage.jsonl"
    # the hook must load in EVERY process of the engine's interpreter (its LLM workers are
    # spawned): a .pth "import" line in that interpreter's site-packages does exactly that
    purelib = subprocess.run([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    (Path(purelib) / "usage_hook.py").write_text(USAGE_HOOK)
    (Path(purelib) / "usage_hook.pth").write_text("import usage_hook\n")
    os.environ["SIMPLETES_USAGE_LOG"] = str(usage_log)
    cmd = [py, "main.py",
           "--init-program", str(task_dir / evolve_file),
           "--evaluator", str(adapter),
           "--instruction", str(task_dir / "objective.md"),
           "--max-generations", str(a.generations),
           "--num-chains", str(a.num_chains), "--k-candidates", str(a.k_candidates),
           "--num-inspirations", str(a.num_inspirations), "--selector", a.selector,
           "--max-tokens", str(a.max_tokens), "--max-total-tokens", str(a.max_total_tokens),
           "--temperature", str(a.temperature),
           "--gen-concurrency", str(a.gen_concurrency), "--eval-concurrency", str(a.eval_concurrency),
           "--init-eval-repeats", str(a.init_eval_repeats),
           "--save-llm-io", "--skip-preflight",
           "--model", a.model,
           "--api-base", "https://openrouter.ai/api/v1",
           "--api-key", api_key,
           "--eval-timeout", str(a.eval_timeout),
           "--output-path", str(out / "checkpoints")]
    cmd += ["--timeout", str(a.llm_timeout), "--retry", str(a.llm_retry)]
    if a.no_stream_k:
        cmd.append("--no-stream-k-candidates")
    cmd += ["--log-interval", str(a.checkpoint_interval)]
    if not a.reflection:
        cmd.append("--disable-reflection")
    # Modal re-runs a preempted call from the top; resume from the latest checkpoint instead
    # the engine resumes from the directory that holds nodes.json (a db_state_* snapshot),
    # not from the instance directory above it
    snaps = [d for d in (out / "checkpoints").glob("*/instance-*/db_state_*")
             if any(d.glob("nodes.json*"))]
    if snaps:
        latest = max(snaps, key=lambda d: d.stat().st_mtime)
        cmd += ["--resume", str(latest)]
        print(f"resuming from {latest}", flush=True)
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
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    def _add_usage(tu):
        if tu:
            usage["prompt_tokens"] += int(tu.get("prompt_tokens", 0) or 0)
            usage["completion_tokens"] += int(tu.get("completion_tokens", 0) or 0)
    meta = json.loads(metas[-1].read_text()) if metas else {}
    # prompts issued = attempts minus the ones cancelled at shutdown; rejected generations
    # (empty output, missing markers) still cost a prompt and are counted
    # the engine counts attempts per CANDIDATE (k per prompt): prompts = attempts / k when the
    # request log is missing; the request log (below) is authoritative when present
    usage["calls"] = (int(meta.get("generation_attempts", 0) or 0) - int(meta.get("generation_cancellations", 0) or 0)) // max(1, a.k_candidates)
    if nodesf and nodesf[-1].suffix == ".json":
        nodes = json.loads(nodesf[-1].read_text())
        for n in nodes:                       # per-node token usage, saved by --save-llm-io
            _add_usage(n.get("token_usage") or {})
    for fp in ckroot.rglob("failure.json"):
        try:
            for f in json.loads(fp.read_text()):
                _add_usage(f.get("token_usage") or {})
        except Exception:
            pass
    if usage_log.exists():                 # exact: every request the engine made
        recs = [json.loads(l) for l in usage_log.read_text().splitlines() if l.strip()]
        if recs:
            usage = {"calls": len(recs), "prompt_tokens": sum(r["prompt_tokens"] for r in recs),
                     "completion_tokens": sum(r["completion_tokens"] for r in recs), "source": "litellm callback"}
        rows = [(n.get("created_at") or n.get("generation") or i,
                 (n.get("metrics") or {}).get("combined_score", n.get("score")))
                for i, n in enumerate(nodes)]
        rows = [(t, s) for t, s in rows if isinstance(s, (int, float))]
        rows.sort(key=lambda r: r[0])
        history = [{"n": i + 1, "score": float(s), "valid": 1.0}
                   for i, (_, s) in enumerate(rows)]
        if best is None and history:
            best = max(h["score"] for h in history)

    # a failed engine run must not look finished: its summary goes to summary_failed.json
    summary_name = "summary.json" if rc == 0 else "summary_failed.json"
    json.dump({"task": a.task, "evolve": evolve_file, "method": "simpletes_upstream",
               "model": a.model, "seed": a.seed,
               "operator": {"class": "UpstreamSimpleTES", "sha": UPSTREAM_SHA},
               "search": {"engine": "upstream", "generations": a.generations,
                          "num_chains": a.num_chains, "k_candidates": a.k_candidates,
                          "num_inspirations": a.num_inspirations, "selector": a.selector,
                          "reflection": a.reflection, "max_total_tokens": a.max_total_tokens},
               "llm_usage": usage, "eval_usage": {"calls": len(history)},
               "eval_server": a.eval_server or None,
               "eval_server_boots": sorted({json.loads(l)["boot_id"] for l in open(boot_log)}
                                           if boot_log.exists() else []),
               "items_run": len(history),
               "failures": int(meta.get("generation_failures", 0) or 0) + len(list(ckroot.rglob("failure.json")) and
                                                                            [f for fp in ckroot.rglob("failure.json") for f in json.loads(fp.read_text())]),
               "best_combined_score": best if best is not None else 0.0,
               "seed_combined_score": history[0]["score"] if history else 0.0,
               "seconds": time.time() - t0, "history": history},
              open(out / summary_name, "w"), indent=1)
    print(f"-> {out}/{summary_name}  best={best}  nodes={len(history)}  rc={rc}")
    sys.exit(0 if rc == 0 else rc)


if __name__ == "__main__":
    main()

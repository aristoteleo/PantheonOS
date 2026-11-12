#!/usr/bin/env python
"""Run upstream OpenEvolve, ShinkaEvolve, AIDE or CORAL on a bench AHC task, from a given program, on the bench's eval server.

    python run_upstream_other.py --optimizer openevolve|shinka|aide|coral --task ahc058 --seed-file F --evals 120 \
        --eval-server URL --output OUT [--seed 0] [--max-llm-calls 600] [--max-llm-usd 2] [--inspiration-file F]

For the handoff test: every arm starts from the same donor elite and spends the same number of
evaluations; this one hands the elite to an optimizer the PantheonOS bench does not implement. Runs
in-process with the optimizer's own interpreter (bioopt-bench's image: /opt/venvs/<optimizer>, the
bench's pinned and patched checkouts under /opt). What is matched to the other arms:
  * model deepseek/deepseek-v4.1-flash on OpenRouter with reasoning off (the arms' --reasoning-off):
    every chat-completions request of the process tree gets extra_body reasoning.enabled=false
    (a sitecustomize on PYTHONPATH, so worker processes carry it), and is logged with its cost
  * the task text (tasks/<task>/objective.md) and the evaluation (the block's eval server)
  * the budget: --evals children after the start program; --max-llm-calls is a safety cap
Every evaluation is logged with its program (evals.jsonl, progs/<sha>.cpp), the shape the
re-measurement reads; summary.json carries the arms' fields (seed / best score, llm usage, seconds).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()

EVALUATOR = r'''
"""Score a program on the bench's eval server; log it. Standard library only."""
import hashlib, json, os, sys, time, urllib.request

URL = {url!r}
TASK = {task!r}
OUT = {out!r}
TOKEN = hashlib.sha256(("evolve-eval:" + os.environ.get("OPENROUTER_API_KEY", "")).encode()).hexdigest()[:32]


def score(code):
    body = json.dumps({{"token": TOKEN, "task": TASK, "fidelity": "full",
                       "files": {{"solution.cpp": code}}}}).encode()
    err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, data=body, headers={{"Content-Type": "application/json"}})
            with urllib.request.urlopen(req, timeout=900) as r:
                b = json.loads(r.read())
            break
        except Exception as e:  # noqa: BLE001
            err = "eval server unreachable: %s: %s" % (type(e).__name__, str(e)[:200])
            time.sleep(5 * (attempt + 1))
    else:
        b = {{"success": False, "error": err}}
    m = b.get("metrics") or {{}}
    sc = m.get("combined_score") if b.get("success") else None
    sha = hashlib.sha256(code.encode()).hexdigest()[:16]
    os.makedirs(os.path.join(OUT, "progs"), exist_ok=True)
    p = os.path.join(OUT, "progs", sha + ".cpp")
    if not os.path.exists(p):
        open(p, "w").write(code)
    with open(os.path.join(OUT, "evals.jsonl"), "a") as f:
        f.write(json.dumps({{"t": time.time(), "sha": sha, "score": sc, "error": b.get("error")}}) + "\n")
    return (float(sc) if sc is not None else 0.0), (None if sc is not None else str(b.get("error"))[:400])
'''

OE_SUFFIX = r'''
from openevolve.evaluation_result import EvaluationResult


def evaluate(program_path):
    sc, err = score(open(program_path).read())
    return EvaluationResult(metrics={"combined_score": sc}, artifacts={"error": err} if err else {})
'''

SHINKA_SUFFIX = r'''

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--program_path", required=True)
    ap.add_argument("--results_dir", required=True)
    a = ap.parse_args()
    sc, err = score(open(a.program_path).read())
    os.makedirs(a.results_dir, exist_ok=True)
    json.dump({"correct": err is None, "error": err}, open(os.path.join(a.results_dir, "correct.json"), "w"))
    json.dump({"combined_score": sc}, open(os.path.join(a.results_dir, "metrics.json"), "w"))
'''


LLM_PATCH = r'''
"""Reasoning off and a usage log for every OpenAI chat-completions or Responses call of this process TREE: loaded as
sitecustomize, so the optimizers' worker processes (OpenEvolve evolves in a process pool) carry it."""
import json, os
_LOG, _CAP = os.environ.get("HOFF_LLM_LOG"), int(os.environ.get("HOFF_LLM_CAP") or 0)
_USD = float(os.environ.get("HOFF_LLM_USD") or 0)
try:
    import openai.resources.chat.completions as _cc
except Exception:  # noqa: BLE001 -- an interpreter without openai (an evaluator subprocess)
    _cc = None
if _LOG and _cc is not None:
    def _calls():
        try:
            with open(_LOG) as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    def _spent():
        try:
            with open(_LOG) as f:
                return sum(json.loads(x).get("usd", 0.0) for x in f if x.strip())
        except FileNotFoundError:
            return 0.0

    def _prep(kw):
        if _CAP and _calls() >= _CAP:
            raise RuntimeError("handoff arm: LLM call cap reached")
        if _USD and _spent() >= _USD:
            raise RuntimeError("handoff arm: LLM dollar cap reached")
        kw.pop("reasoning_effort", None)
        eb = dict(kw.get("extra_body") or {})
        eb["reasoning"] = {"enabled": False}
        eb["usage"] = {"include": True}      # OpenRouter reports the call's cost
        kw["extra_body"] = eb
        return kw

    def _book(r):
        u = getattr(r, "usage", None)
        c = getattr(u, "cost", None) if u is not None else None
        if c is None and u is not None and getattr(u, "model_extra", None):
            c = u.model_extra.get("cost")
        with open(_LOG, "a") as f:
            f.write(json.dumps({"p": getattr(u, "prompt_tokens", 0) or getattr(u, "input_tokens", 0) or 0,
                                "c": getattr(u, "completion_tokens", 0) or getattr(u, "output_tokens", 0) or 0,
                                "usd": float(c or 0.0)}) + "\n")
        return r

    def _wrap(cls, responses=False):
        sync_create = cls.create

        def create(self, *a, **kw):
            if responses:
                kw.pop("reasoning", None)   # the Responses API carries it top-level; ours goes in extra_body
            return _book(sync_create(self, *a, **_prep(kw)))
        cls.create = create

    def _awrap(cls, responses=False):
        async_create = cls.create

        async def create(self, *a, **kw):
            if responses:
                kw.pop("reasoning", None)
            return _book(await async_create(self, *a, **_prep(kw)))
        cls.create = create

    _wrap(_cc.Completions)
    _awrap(_cc.AsyncCompletions)
    # ShinkaEvolve talks to OpenRouter through the Responses API (POST /responses), not chat completions
    try:
        import openai.resources.responses as _rr
        _wrap(_rr.Responses, responses=True)
        _awrap(_rr.AsyncResponses, responses=True)
    except Exception:  # noqa: BLE001
        pass
'''


def install_llm_patch(out: Path, cap: int, usd: float = 0.0) -> None:
    d = out / "llmpatch"
    d.mkdir(exist_ok=True)
    (d / "sitecustomize.py").write_text(LLM_PATCH)
    os.environ["HOFF_LLM_LOG"] = str(out / "llm_calls.jsonl")
    os.environ["HOFF_LLM_CAP"] = str(cap)
    os.environ["HOFF_LLM_USD"] = str(usd or 0)
    os.environ["PYTHONPATH"] = str(d) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    sys.path.insert(0, str(d))
    import sitecustomize  # noqa: F401  -- this process too


def llm_usage(out: Path) -> dict:
    rows = [json.loads(x) for x in (out / "llm_calls.jsonl").read_text().splitlines()] if (out / "llm_calls.jsonl").exists() else []
    return {"calls": len(rows), "prompt_tokens": sum(r["p"] for r in rows),
            "completion_tokens": sum(r["c"] for r in rows), "cost_usd": sum(r["usd"] for r in rows)}


T0 = time.time()


def write_summary(out: Path, stopped_on: str = "done") -> None:
    rows = [json.loads(x) for x in (out / "evals.jsonl").read_text().splitlines()] if (out / "evals.jsonl").exists() else []
    ok = [r for r in rows if r["score"] is not None]
    a = ARGS
    (out / "summary.json").write_text(json.dumps({
        "task": a.task, "evolve": "solution.cpp", "method": f"{a.optimizer}_upstream", "model": a.model,
        "seed": a.seed, "seed_file": a.seed_file, "eval_server": a.eval_server,
        "seed_combined_score": rows[0]["score"] if rows else None,
        "best_combined_score": max((r["score"] for r in ok), default=None),
        "items_run": max(len(rows) - 1, 0), "failures": len(rows) - len(ok),
        "eval_usage": {"calls": len(rows)}, "llm_usage": llm_usage(out), "stopped_on": stopped_on,
        "seconds": time.time() - T0}, indent=1))


def run_openevolve(a, work: Path, task_text: str) -> None:
    (work / "initial_program.cpp").write_text(Path(a.seed_file).read_text())
    (work / "evaluator.py").write_text(EVALUATOR.format(url=a.eval_server, task=a.task, out=str(a.output)) + OE_SUFFIX)
    # the bench's OpenEvolve settings (bioopt-bench modal_optimizer.run_openevolve), C++ instead of Python
    (work / "config.yaml").write_text(f"""
max_iterations: {a.evals}
checkpoint_interval: 5
random_seed: {a.seed}
language: cpp
file_suffix: .cpp
diff_based_evolution: true
max_code_length: 120000
llm:
  api_base: "https://openrouter.ai/api/v1"
  api_key: "{os.environ['OPENROUTER_API_KEY']}"
  primary_model: "{a.model}"
  primary_model_weight: 1.0
  temperature: 1.0
  max_tokens: 32768
  timeout: 900
  retries: 3
prompt:
  system_message: |
{chr(10).join('    ' + ln for ln in task_text.splitlines())}
database:
  population_size: 40
  archive_size: 20
  num_islands: 3
evaluator:
  timeout: 1800
  cascade_evaluation: false
  parallel_evaluations: 2
""")
    os.chdir(work)
    sys.argv = ["openevolve-run.py", "initial_program.cpp", "evaluator.py", "--config", "config.yaml",
                "--iterations", str(a.evals), "--output", "out"]
    import runpy
    runpy.run_path("/opt/openevolve/openevolve-run.py", run_name="__main__")


def run_shinka(a, work: Path, task_text: str) -> None:
    (work / "initial.cpp").write_text(Path(a.seed_file).read_text())
    (work / "evaluate.py").write_text(EVALUATOR.format(url=a.eval_server, task=a.task, out=str(a.output)) + SHINKA_SUFFIX)
    os.chdir(work)
    # Shinka resolves a model's provider from its pricing table; without a row it never calls the
    # model (bioopt-bench measured 3.4 h of zero LLM calls). Rates are a self-report only.
    import shinka.llm.providers.pricing as pr
    csv = Path(pr.__file__).parent / "pricing.csv"
    t = csv.read_text()
    if a.model + "," not in t:
        with csv.open("a") as f:
            f.write(("" if t.endswith("\n") else "\n") + a.model + ",openrouter,0.14,0.28,,,,True,1,1\n")
    from shinka.core import ShinkaEvolveRunner, EvolutionConfig
    from shinka.database import DatabaseConfig
    from shinka.launch import LocalJobConfig
    # the bench's pinned Shinka settings (optimizers/shinka/shinka_config.yaml), C++, two evaluations
    # in flight like the other arms
    import dataclasses

    def only(cls, **kw):
        names = {f.name for f in dataclasses.fields(cls)}
        dropped = sorted(set(kw) - names)
        if dropped:
            print("shinka: fields not in this version, dropped:", dropped, flush=True)
        return cls(**{k: v for k, v in kw.items() if k in names})

    evo = only(EvolutionConfig,
        num_generations=a.evals + 1, language="cpp", init_program_path="initial.cpp",
        results_dir="results/run", task_sys_msg=task_text, llm_models=[a.model],
        llm_kwargs={"temperatures": [1.0], "max_tokens": 32768},
        patch_types=["diff", "full", "cross"], patch_type_probs=[0.6, 0.3, 0.1],
        max_patch_resamples=3, max_patch_attempts=1, max_novelty_attempts=1, job_type="local",
        embedding_model=None)   # as the bench's pinned config (shinka_config.yaml: embedding_model null)
    db = only(DatabaseConfig, num_islands=2, archive_size=40, elite_selection_ratio=0.3, num_archive_inspirations=1,
                        num_top_k_inspirations=1, migration_interval=10, migration_rate=0.1,
                        island_elitism=True, parent_selection_strategy="weighted", parent_selection_lambda=10.0,
                        archive_criteria={"combined_score": 1.0, "loc": -0.3})
    ShinkaEvolveRunner(evo_config=evo,
                       job_config=LocalJobConfig(eval_program_path="evaluate.py", time="00:30:00",
                                                 python_executable=sys.executable),
                       db_config=db, verbose=True, max_evaluation_jobs=2, max_proposal_jobs=2).run()


def _evals_logged(out: Path) -> int:
    try:
        return sum(1 for x in (out / "evals.jsonl").read_text().splitlines() if x.strip())
    except FileNotFoundError:
        return 0


def _score_in_process(a):
    """The arms' score() in this process: the seed's own read, so evals.jsonl row 0 is the start program
    for AIDE and CORAL as it is for OpenEvolve and Shinka, which evaluate their initial program first."""
    ns: dict = {}
    exec(EVALUATOR.format(url=a.eval_server, task=a.task, out=str(a.output)), ns)
    return ns["score"]


def _run_capped(cmd, cwd: Path, env: dict, a, wall_s: float, spent=None) -> str:
    """Run an optimizer that has no evaluation cap of its own; stop it once --evals children have been
    scored (evals.jsonl holds the seed's read + one row per scored child), the wall clock is spent, or
    (with `spent`, for LLM calls the patch does not see) --max-llm-usd is reached."""
    import signal
    import subprocess
    with open(a.output / "optimizer.log", "ab") as log:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        t0, why = time.time(), "exited"
        while p.poll() is None:
            if _evals_logged(a.output) >= a.evals + 1:
                why = "eval cap"
                break
            if time.time() - t0 > wall_s:
                why = "wall clock"
                break
            if spent is not None and a.max_llm_usd and spent() >= a.max_llm_usd:
                why = "llm dollar cap"
                break
            time.sleep(10)
        if p.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                except ProcessLookupError:
                    break
                time.sleep(15)
    return why


AIDE_BENCH = r"""

from pathlib import Path


def submit(path="solution.cpp"):
    p = Path(path)
    if not p.exists():
        print("SUBMIT ERROR: %s does not exist." % p); print("Validation metric: 0.0"); return 0.0
    code = p.read_text()
    if not code.strip():
        print("SUBMIT ERROR: %s is empty." % p); print("Validation metric: 0.0"); return 0.0
    sc, err = score(code)
    if err:
        print("PROGRAM FAILED:", err[:500]); print("Validation metric: 0.0"); return 0.0
    print("Validation metric: %.6f" % sc)
    return sc
"""

AIDE_HOWTO = """

## How you are scored — read this, it is the only path to a number

There are no test cases on this machine. Write the complete C++ program to `solution.cpp`, then submit it:

```python
import sys; sys.path.insert(0, "./input")   # bench.py and the starting program are in ./input
import bench
bench.submit("solution.cpp")                 # prints "Validation metric: <score>"
```

Your `runfile.py` must have exactly this shape: write the program out, then submit. It does not
compile or run the program itself:

```python
import sys
sys.path.insert(0, "./input")
PROGRAM = r'''
<the complete C++ source of solution.cpp>
'''
open("solution.cpp", "w").write(PROGRAM)
import bench
bench.submit("solution.cpp")
```

Read `./input/seed_program.cpp` ONCE to see the starting point (it scores SEED_SCORE), then put your
improved version in `PROGRAM`. Do not loop over it or print it. Higher is better.
"""


def run_aide(a, work: Path, task_text: str) -> str:
    seed = Path(a.seed_file).read_text()
    s0, _ = _score_in_process(a)(seed)
    data = work / "data"
    data.mkdir(exist_ok=True)
    (data / "seed_program.cpp").write_text(seed)
    (data / "bench.py").write_text(EVALUATOR.format(url=a.eval_server, task=a.task, out=str(a.output)) + AIDE_BENCH)
    # the bench's AIDE task text (bioopt-bench modal_optimizer.run_aide), C++ instead of a Python method
    desc = work / "task.md"
    desc.write_text(task_text.rstrip() + AIDE_HOWTO.replace("SEED_SCORE", "%.6f" % s0))
    env = {**os.environ,
           # AIDE reads the metric with a tool call, which its OpenRouter backend cannot do; with
           # OPENAI_BASE_URL set it routes the model to its OpenAI backend aimed at OpenRouter
           "OPENAI_BASE_URL": "https://openrouter.ai/api/v1", "OPENAI_API_KEY": os.environ["OPENROUTER_API_KEY"]}
    env.pop("PANTHEONEVO_REASONING_EFFORT", None)   # reasoning off, as every arm (the LLM patch)
    out = work / "out"
    cmd = ["/opt/venvs/aide/bin/aide", f"data_dir={data}", f"desc_file={desc}", f"log_dir={out / 'logs'}",
           f"workspace_dir={out / 'ws'}", f"exp_name=aide-s{a.seed}", f"agent.steps={3 * a.evals}",
           f"agent.code.model={a.model}", f"agent.feedback.model={a.model}", f"report.model={a.model}",
           "agent.code.temp=1.0", "agent.feedback.temp=1.0",
           "agent.k_fold_validation=1", "agent.data_preview=False", "agent.metric_maximize=True",
           "generate_report=False", "exec.timeout=1800", "copy_data=True"]
    return _run_capped(cmd, work, env, a, a.wall_hours * 3600)


CORAL_GRADER = r"""

from pathlib import Path
from coral.grader import TaskGrader

TOKEN = TOKEN_VALUE


class Grader(TaskGrader):
    # the vendored commit's interface: evaluate(self) with self.args / self.codebase_path / self.fail / self.score
    def evaluate(self):
        pf = self.args.get("program_file", "solution.cpp")
        path = Path(self.codebase_path) / pf
        if not path.exists():
            return self.fail(pf + " not found in the workspace")
        sc, err = score(path.read_text())
        if err:
            return self.fail("program scored 0: " + err[:300])
        return self.score(float(sc), explanation="combined_score = %.6f" % sc)
"""


# CORAL's claude-code agents reach OpenRouter through LiteLLM inside CORAL's gateway, which the LLM patch
# does not see. The gateway logs every request with its streamed reply; the token counts come from the
# reply's usage events, the price from the per-token rates OpenRouter reported for this model on the
# other arms (least squares over 3,241 calls of the handoff runs: $0.10 / M prompt, $1.06 / M completion).
# Prompt caching is not credited, so for an agent re-sending its context this is an upper bound.
CORAL_RATES = (0.10e-6, 1.06e-6)


def coral_usage(work: Path) -> list[dict]:
    import re
    rows, seen = [], set()
    for f in work.glob("results/*/*/.coral/public/gateway/requests.jsonl"):
        for x in f.read_text().splitlines():
            try:
                r = json.loads(x)
            except ValueError:
                continue
            if r.get("request_id") in seen:      # a run dir is also reachable through CORAL's `latest` link
                continue
            seen.add(r.get("request_id"))
            resp = r.get("response")
            resp = resp if isinstance(resp, str) else json.dumps(resp or {})
            p = c = 0
            for u in re.findall(r'"usage": (\{[^{}]*\})', resp):
                u = json.loads(u)
                p = max(p, u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0))
                c = max(c, u.get("output_tokens", 0))
            rows.append({"p": p, "c": c, "usd": p * CORAL_RATES[0] + c * CORAL_RATES[1], "est": True})
    return rows


def run_coral(a, work: Path, task_text: str) -> str:
    import hashlib as _h
    seed = Path(a.seed_file).read_text()
    _score_in_process(a)(seed)
    (work / "seed").mkdir(exist_ok=True)
    (work / "seed" / "solution.cpp").write_text(seed)
    g = work / "grader" / "src" / "coral_grader"
    g.mkdir(parents=True, exist_ok=True)
    (work / "grader" / "pyproject.toml").write_text(
        '[project]\nname = "coral-grader"\nversion = "0.1.0"\ndependencies = []\n\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/coral_grader"]\n')
    (g / "__init__.py").write_text("from .grader import Grader\n__all__ = ['Grader']\n")
    # the grader runs in CORAL's own venv, whose environment may not carry the key: bake the token in
    token = _h.sha256(("evolve-eval:" + os.environ.get("OPENROUTER_API_KEY", "")).encode()).hexdigest()[:32]
    (g / "grader.py").write_text(EVALUATOR.format(url=a.eval_server, task=a.task, out=str(a.output))
                                 + CORAL_GRADER.replace("TOKEN_VALUE", repr(token)))
    # reasoning off through the gateway, as the bench's CORAL (DeepSeek thinking does not survive
    # claude-code's Anthropic-format path) and as every other arm here
    mdl = "\n".join(f"""  - model_name: {name}
    litellm_params:
      model: openai/{a.model}
      api_base: https://openrouter.ai/api/v1
      api_key: os.environ/OPENROUTER_API_KEY
      temperature: 1.0
      extra_body:
        reasoning:
          enabled: false
        include_reasoning: false""" for name in ("claude-sonnet-5", "sonnet"))
    (work / "litellm_config.yaml").write_text("model_list:\n" + mdl
                                              + "\nlitellm_settings:\n  drop_params: true\n  request_timeout: 900\n")
    desc = "\n".join("    " + ln for ln in task_text.splitlines())
    (work / "task.yaml").write_text(f"""
task:
  name: "{a.task}"
  description: |
{desc}

    Only a call to the grader gives a real score: there are no test cases on this machine.
    Iterate: edit solution.cpp, submit, read the score, improve.
agents:
  count: {a.coral_agents}
  runtime: claude_code
  model: sonnet
  gateway:
    enabled: true
    config: ./litellm_config.yaml
workspace:
  repo_path: "./seed"
  results_dir: "./results"
grader:
  entrypoint: "coral_grader.grader:Grader"
  setup:
    - "uv pip install -e ./grader"
  timeout: 1800
  direction: maximize
  args:
    program_file: "solution.cpp"
""")
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ["OPENROUTER_API_KEY"], "DISABLE_TELEMETRY": "1",
           "IS_SANDBOX": "1",
           # CORAL installs itself into the grader venv (hatch-vcs -> git describe); no git needed with these
           "SETUPTOOLS_SCM_PRETEND_VERSION": "0.7.10", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CORAL": "0.7.10"}
    port = 14500 + (int(os.environ.get("SLURM_JOB_ID") or 0) + a.seed) % 4000
    cmd = ["/opt/venvs/coral/bin/coral", "start", "-c", "task.yaml", f"agents.count={a.coral_agents}",
           f"run.stop.max_real_attempts={a.evals}", "run.session=local", f"agents.gateway.port={port}"]
    why = _run_capped(cmd, work, env, a, a.wall_hours * 3600,
                      spent=lambda: sum(r["usd"] for r in coral_usage(work)))
    with open(a.output / "llm_calls.jsonl", "a") as f:      # the summary's llm_usage reads this file
        for r in coral_usage(work):
            f.write(json.dumps(r) + "\n")
    return why


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--optimizer", required=True, choices=["openevolve", "shinka", "aide", "coral"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--seed-file", required=True)
    ap.add_argument("--evals", type=int, default=120)
    ap.add_argument("--eval-server", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default="deepseek/deepseek-v4.1-flash")
    ap.add_argument("--max-llm-calls", type=int, default=600)
    ap.add_argument("--max-llm-usd", type=float, default=0.0, help="dollar cap on the run's LLM calls (0 = none)")
    ap.add_argument("--inspiration-file", default=None,
                    help="append this text (reference programs from earlier runs) to the task text")
    ap.add_argument("--wall-hours", type=float, default=6.0, help="AIDE / CORAL: stop after this long")
    ap.add_argument("--coral-agents", type=int, default=4)
    ap.add_argument("--resume", action="store_true", help="accepted for arm.sbatch; a rerun starts over")
    ARGS = a = ap.parse_args()
    a.output = Path(a.output).resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    work = a.output / "work"
    work.mkdir(exist_ok=True)
    task_text = (HERE / "tasks" / a.task / "objective.md").read_text()
    if a.inspiration_file:
        task_text = task_text.rstrip() + "\n\n" + Path(a.inspiration_file).read_text().strip() + "\n"
    install_llm_patch(a.output, a.max_llm_calls, a.max_llm_usd)
    stopped = "done"
    try:
        stopped = {"openevolve": run_openevolve, "shinka": run_shinka, "aide": run_aide,
                   "coral": run_coral}[a.optimizer](a, work, task_text) or "done"
    except SystemExit:
        pass
    finally:
        write_summary(a.output, stopped)

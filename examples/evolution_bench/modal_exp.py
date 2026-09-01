"""Run bench experiments wholly on Modal -- nothing executes on the launching machine.

    modal run --detach modal_exp.py --spec specs/wave1.json     # launch a wave
    modal run modal_exp.py --collect EXP                        # print summaries from the volume

One arm = one container = one `run_bench.py` invocation. The spec file is a JSON list of arms:

    [{"out": "wave1/e1_llm_s0",
      "argv": ["--task", "ahc039", "--method", "annealed", "--iterations", "30", "--seed", "0"]},
     ...]

`chain` arms run several invocations SEQUENTIALLY in one container, threading `--judge-state`
through a file on the volume -- that is the whole point of a chain:

    [{"chain": [{"out": "wave2/chainA_r1", "argv": [...]},
                {"out": "wave2/chainA_r2", "argv": [...]}],
      "judge_state": "wave2/chainA/judge.json"}]

Design notes, each of which cost something to learn:

  * The image is built FROM the ale-bench contest image, so the g++-12/cpp20 toolchain the
    scores depend on is byte-identical to the local Docker path. `AHC_EXEC=direct` then runs the
    case runner in-process -- a Modal container cannot host a Docker daemon, and does not need
    to: it IS the disposable sandbox that `docker run --network=none` fakes locally. One
    consequence worth remembering when comparing numbers: local AHC evals ran under x86 emulation
    on Apple silicon (~65% throughput), Modal runs native, so absolute scores are higher here.
    Every arm shares the hardware, so arm-to-arm comparison is unaffected.
  * Dependencies are an image layer built from `pyproject.toml`; the repo itself is a runtime
    mount on PYTHONPATH. Editing an experiment script therefore never rebuilds the image.
  * Results stream to a Volume, committed every couple of minutes, so a wave can be watched (and
    a dead run diagnosed) from outside while it runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import modal

HERE = Path(__file__).parent
REPO = HERE.parent.parent

app = modal.App("evolve-exp")
vol = modal.Volume.from_name("evolve-exp-results", create_if_missing=True)

image = (
    modal.Image.from_registry("yimjk/ale-bench:cpp20-202301", add_python="3.12")
    .pip_install_from_pyproject(str(REPO / "pyproject.toml"))
    .env({"AHC_EXEC": "direct", "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/repo"})
    .add_local_dir(
        str(REPO), "/repo",
        ignore=["**/.git/**", "**/.venv/**", "**/__pycache__/**", "**/node_modules/**",
                "**/.manim/**", "**/results/**", "**/results_*/**", "**/*.log",
                ".claude/**", "**/.pytest_cache/**", "docs/**", "tests/**"],
    )
)


def _run_one(argv: list, out: str, judge_state: str | None = None,
             script: str = "examples/evolution_bench/run_bench.py") -> dict:
    """One runner invocation, log streamed to the volume as it goes. `script` lets an arm run
    a different repo-relative runner (e.g. the upstream-SimpleTES adapter)."""
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, f"/repo/{script}",
           *[str(a) for a in argv], "--output", out]
    if judge_state:
        os.makedirs(os.path.dirname(judge_state), exist_ok=True)
        cmd += ["--judge-state", judge_state]
    t0 = time.time()
    with open(f"{out}/run.log", "w") as lf:
        lf.write(" ".join(cmd) + "\n\n")
        lf.flush()
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True)
        while p.poll() is None:
            time.sleep(120)
            vol.commit()
        rc = p.returncode
    vol.commit()
    summary = {}
    sp = Path(out) / "summary.json"
    if sp.exists():
        summary = json.loads(sp.read_text())
    return {"rc": rc, "out": out.removeprefix("/results/"),
            "best": summary.get("best_combined_score"),
            "seed_score": summary.get("seed_combined_score"),
            "items": summary.get("items_run"), "failures": summary.get("failures"),
            "seconds": round(time.time() - t0)}


@app.function(image=image, secrets=[modal.Secret.from_name("evolve-exp-openrouter")],
              volumes={"/results": vol}, cpu=8.0, memory=16384, timeout=24 * 3600,
              max_containers=12)
def run_arm(argv: list, out_rel: str,
            script: str = "examples/evolution_bench/run_bench.py") -> dict:
    # max_containers bounds the wave's concurrency: the OpenRouter key is shared, and a wave of
    # 24 arms x 2 workers all streaming at once turns into 429s that pollute the arms unevenly.
    return _run_one(argv, f"/results/{out_rel}", script=script)


@app.function(image=image, secrets=[modal.Secret.from_name("evolve-exp-openrouter")],
              volumes={"/results": vol}, cpu=8.0, memory=16384, timeout=20 * 3600)
def run_chain(stages: list, judge_state_rel: str) -> list:
    """Sequential runs sharing one judge-state file. 20h cap: three full runs plus slack."""
    results = []
    for st in stages:
        results.append(_run_one(st["argv"], f"/results/{st['out']}",
                                judge_state=f"/results/{judge_state_rel}"))
        if results[-1]["rc"] != 0:
            break                             # a dead run trains nothing; stop the chain
    return results


@app.local_entrypoint()
def main(spec: str = "", collect: str = ""):
    if collect:
        print(json.dumps(summaries.remote(collect), indent=1))
        return
    arms = json.loads(Path(spec).read_text())
    calls = []
    for arm in arms:
        if "chain" in arm:
            c = run_chain.spawn(arm["chain"], arm["judge_state"])
            name = arm["chain"][0]["out"] + f" (chain of {len(arm['chain'])})"
        else:
            c = run_arm.spawn(arm["argv"], arm["out"],
                              script=arm.get("script",
                                             "examples/evolution_bench/run_bench.py"))
            name = arm["out"]
        calls.append((name, c))
        print(f"spawned {name:42} {c.object_id}")
    ids = {n: c.object_id for n, c in calls}
    Path(spec).with_suffix(".calls.json").write_text(json.dumps(ids, indent=1))
    print(f"\n{len(calls)} calls; ids -> {Path(spec).with_suffix('.calls.json')}")


@app.function(image=image, volumes={"/results": vol}, timeout=600)
def summaries(prefix: str) -> list:
    """Every summary.json under a prefix -- the collect path for a finished (or running) wave.

    Two numbers the summary alone cannot give, read from the store:

      * `seed_own`: what the SEED measured on THIS container. Containers differ in CPU
        throughput and AHC scores are wall-clock-limited, so absolute bests carry a per-container
        offset; the honest cross-arm comparison is `gain = best - seed_own`.
      * `hist_max`: the best single measurement. Content-identical resubmissions re-measure one
        individual and the latest reading wins, so `best` can sit a noise-width below the best
        roll of the same program. The difference is the eval noise band, worth seeing.
    """
    vol.reload()
    out = []
    for p in sorted(Path(f"/results/{prefix}").rglob("summary.json")):
        s = json.loads(p.read_text())
        seed_own = None
        kids = {"n": 0, "improved": 0, "wrecked": 0}
        sp = p.parent / "store.json"
        if sp.exists():
            inds = json.loads(sp.read_text())["individuals"]
            if isinstance(inds, dict):
                inds = list(inds.values())
            for i in inds:
                if not i.get("parent_ids") and i.get("kind") == "code":
                    for m in i.get("measurements", []):
                        v = m.get("metrics", {}).get("combined_score")
                        if v is not None:
                            seed_own = v
                    break
            # Which kind of children a method writes: improvements, noise, or wreckage. A
            # "wrecked" child compiled and ran and lost more than 0.5 -- on this task that means
            # the incumbent's structure did not survive the edit.
            if seed_own is not None:
                for i in inds:
                    if i.get("kind") != "code" or not i.get("parent_ids"):
                        continue
                    v = None
                    for m in i.get("measurements", []):
                        mt = m.get("metrics", {})
                        if "combined_score" in mt and mt.get("validity", 1) > 0:
                            v = mt["combined_score"]
                    if v is None:
                        continue
                    kids["n"] += 1
                    kids["improved"] += v > seed_own + 0.001
                    kids["wrecked"] += v < seed_own - 0.5
        hist = [h["score"] for h in s.get("history", [])]
        out.append({"arm": str(p.parent).removeprefix("/results/"),
                    "best": s.get("best_combined_score"),
                    "task": s.get("task"), "method": s.get("method"),
                    "history": s.get("history", []),
                    "seed_own": seed_own,
                    "gain": (s.get("best_combined_score") - seed_own)
                            if seed_own is not None else None,
                    "hist_max": max(hist) if hist else None,
                    "kids": kids,
                    "items": s.get("items_run"), "failures": s.get("failures"),
                    "seconds": round(s.get("seconds", 0)),
                    "search": s.get("search", {})})
    return out

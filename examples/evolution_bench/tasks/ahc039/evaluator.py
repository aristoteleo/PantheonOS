"""AHC039 -- Purse Seine Fishing. The first task here whose gains do not go asymptotic.

Given 5000 mackerels and 5000 sardines on a plane, draw an axis-aligned rectilinear polygon
maximising (mackerels inside - sardines inside), under vertex, perimeter and self-intersection
limits. Scored as the MEAN over 150 official test cases, each with a 2-second limit.

Why this problem and not another construction puzzle. Four problems were screened out first --
Erdos, circle packing, Hadamard n=29, sumset-vs-difference -- and all four failed the same way: one
good mutation took most of the available gain and every later improvement shrank geometrically
until it sat below the run-to-run noise. That is what a fixed target does. Here:

  * the score is an AVERAGE OVER 150 INDEPENDENT CASES, so improvements stay additive -- there is
    always another case to fix, and no single insight collects the whole prize
  * there is no known optimum. It is a heuristic contest; the leaderboard still moves
  * the seed is already a 5th-place solution (~3700/case against a 5000 target), so the search
    cannot win by replacing something naive

Two honest caveats. The tester is a Linux x86-64 binary and this runs under emulation on Apple
silicon at about 65% of native throughput; since AHC scoring is wall-clock-limited, absolute
numbers here are NOT comparable with AtCoder's leaderboard. The handicap is uniform across arms, so
an internal comparison is unaffected. And the evaluation is genuinely noisy -- three repeats of the
seed gave 2.47339 / 2.47155 / 2.47244 -- which is why it is repeated and averaged, and why any
comparison has to weigh a method difference against this as well as against seed-to-seed spread.

Setup, once:

    bash tasks/ahc039/fetch_cache.sh        # 38MB of inputs + the tester binary
    docker pull --platform linux/amd64 yimjk/ale-bench:cpp20-202301
"""
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

# NOT `Path(__file__).parent`. CodeEvaluator exec()s this source, and in that namespace `__file__`
# does not exist -- referring to it at module scope raises NameError before `evaluate` is even
# defined, and the harness reports that as a failed evaluation of the program. The task directory
# therefore arrives by environment; `run_bench.py` sets it, and the fallback keeps a direct
# `python evaluator.py` working.
HERE = Path(os.environ.get("AHC_TASK_DIR") or globals().get("__file__", ".")).resolve()
if HERE.is_file():
    HERE = HERE.parent
PROBLEM_ID = "ahc039"
TIME_LIMIT = 2.0
FULL_CASES = 150

DOCKER_IMAGE = os.environ.get("AHC_DOCKER_IMAGE", "yimjk/ale-bench:cpp20-202301")
PLATFORM = os.environ.get("AHC_DOCKER_PLATFORM", "linux/amd64")
CACHE_DIR = Path(os.environ.get("AHC_CACHE_DIR", str(HERE / "cache")))
RUNNER = Path(os.environ.get("AHC_RUNNER_SCRIPT", str(HERE / "docker_runner.py")))
CASE_WORKERS = int(os.environ.get("AHC_CASE_WORKERS", "8"))
DOCKER_TIMEOUT = int(os.environ.get("AHC_DOCKER_TIMEOUT", "900"))

# How many cases and repeats each fidelity uses. `low` exists so the agent's inner loop can measure
# something in ~25s instead of ~124s; the loop's own measurement always uses `full`, so a cheap
# reading can never become the recorded score.
FIDELITY = {
    "low": (int(os.environ.get("AHC_LOW_CASES", "30")), 1),
    "full": (int(os.environ.get("AHC_FULL_CASES", str(FULL_CASES))),
             int(os.environ.get("AHC_EVAL_RUNS", "3"))),
}


def _invalid(reason: str, t0: float, **extra) -> Dict[str, Any]:
    return {"combined_score": 0.0, "raw_score": 0.0, "validity": 0.0,
            "num_accepted": 0, "eval_time": time.time() - t0,
            "invalid_reason": reason,
            "fitness_weights": {"combined_score": 1.0}, **extra}


def _run_once(tmp_dir: Path, n_cases: int) -> Dict[str, Any]:
    if os.environ.get("AHC_EXEC") == "direct":
        # For environments that ARE the container: a Modal image built FROM the ale-bench image
        # has the identical g++-12/cpp20 toolchain, no Docker daemon, and is itself disposable --
        # the isolation `docker run --network=none` provides locally is already the platform's.
        # Same runner script, host paths instead of mounts.
        cmd = ["python3", str(RUNNER), str(tmp_dir / "Main.cpp"),
               str(CACHE_DIR / "public_inputs_150" / f"{PROBLEM_ID}_inputs"),
               str(CACHE_DIR / "tester_binaries" / f"{PROBLEM_ID}_tester"),
               str(n_cases), str(CASE_WORKERS), str(TIME_LIMIT)]
    else:
        cmd = ["docker", "run", "--rm", "--network=none", "--platform", PLATFORM,
               "-v", f"{tmp_dir}:/work:ro",
               "-v", f"{CACHE_DIR}:/cache:ro",
               "-v", f"{RUNNER}:/runner.py:ro",
               DOCKER_IMAGE, "python3", "/runner.py",
               "/work/Main.cpp",
               f"/cache/public_inputs_150/{PROBLEM_ID}_inputs",
               f"/cache/tester_binaries/{PROBLEM_ID}_tester",
               str(n_cases), str(CASE_WORKERS), str(TIME_LIMIT)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    line = next((ln for ln in reversed((p.stdout or "").splitlines())
                 if ln.strip().startswith("{")), None)
    if line is None:
        return {"error": (p.stderr or p.stdout or "no output from runner")[-500:]}
    return json.loads(line)


def _diagnosis(text: str, limit: int = 900) -> str:
    """The part of a compiler's output that says what is wrong.

    g++ prints warnings before errors, so truncating from the front hands back a list of
    `-Wsign-compare` notices and cuts off the error that actually stopped the build. The agent gets
    this text as its feedback: one run's mutation failed twice in a row against a message that
    contained no error at all, which is not something it could have acted on.
    """
    text = text or ""
    errors = [ln for ln in text.splitlines() if " error:" in ln or "fatal error:" in ln]
    if errors:
        head = "\n".join(errors[:8])
        return head[:limit] + (" ..." if len(head) > limit else "")
    return text[:limit] + (" ..." if len(text) > limit else "")


def evaluate(workspace_path: str, fidelity: str = "full") -> Dict[str, Any]:
    t0 = time.time()
    # The harness has no way to pass an argument -- it hands over a workspace path and nothing
    # else -- so a requested fidelity arrives as a file in that workspace. Per workspace rather
    # than per process, because several mutations measure concurrently and a shared setting would
    # let one agent's cheap reading be recorded as another's score.
    marker = Path(workspace_path) / ".fidelity"
    if marker.exists():
        fidelity = marker.read_text().strip() or fidelity
    n_cases, n_runs = FIDELITY.get(fidelity, FIDELITY["full"])

    src = Path(workspace_path) / "solution.cpp"
    if not src.exists():
        return _invalid("solution.cpp not found in the workspace", t0)
    if not (CACHE_DIR / "public_inputs_150" / f"{PROBLEM_ID}_inputs").exists():
        return _invalid(f"test inputs missing under {CACHE_DIR}; run fetch_cache.sh", t0)
    if not (CACHE_DIR / "tester_binaries" / f"{PROBLEM_ID}_tester").exists():
        return _invalid(f"tester binary missing under {CACHE_DIR}; run fetch_cache.sh", t0)

    tmp = Path(tempfile.mkdtemp(prefix="ahc039_"))
    try:
        shutil.copyfile(src, tmp / "Main.cpp")
        runs = []
        for _ in range(n_runs):
            r = _run_once(tmp, n_cases)
            if r.get("error"):
                return _invalid(_diagnosis(str(r["error"])), t0)
            runs.append(r)
    except subprocess.TimeoutExpired:
        return _invalid(f"docker run exceeded {DOCKER_TIMEOUT}s", t0)
    except Exception as e:  # noqa: BLE001
        return _invalid(f"{type(e).__name__}: {e}", t0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The runner reports per case; the aggregate is ours to compute. It returns `case_results`
    # only -- there is no top-level total -- and reading one that is not there silently scores
    # every run as a total rejection.
    accepted, totals, first_bad = [], [], ""
    for r in runs:
        cases = r.get("case_results") or []
        ok = [c for c in cases if c.get("judge") == "AC"]
        accepted.append(len(ok))
        totals.append(float(sum(c.get("score", 0) or 0 for c in cases)))
        if not first_bad:
            bad = next((c for c in cases if c.get("judge") != "AC"), None)
            if bad:
                first_bad = f"case {bad.get('case_idx')}: {bad.get('judge')} {bad.get('msg', '')}"

    if min(accepted) < n_cases:
        # A rejected case scores 0 in AHC -- it is not skipped. Averaging over the survivors would
        # pay a program for the cases it failed, so a run with any rejection is reported as
        # infeasible and the reason is carried out to whoever reads the measurement.
        return _invalid(f"{n_cases - min(accepted)}/{n_cases} cases rejected -- {first_bad}"[:300],
                        t0, num_accepted=min(accepted))

    per_case = [t / n_cases for t in totals]
    raw = statistics.mean(per_case)
    return {
        "combined_score": float(raw / 1500.0),   # SimpleTES's normalisation: seed ~2.47
        "raw_score": float(raw),
        "total_score": float(statistics.mean(totals)),
        "score_spread": float(max(per_case) - min(per_case)) if len(per_case) > 1 else 0.0,
        "num_accepted": n_cases,
        "num_cases": n_cases,
        "n_eval_runs": n_runs,
        "validity": 1.0,
        "eval_time": time.time() - t0,
        "fitness_weights": {"combined_score": 1.0},
    }


if __name__ == "__main__" and "__file__" in globals():
    # The `__file__` guard matters: CodeEvaluator exec()s this source with __name__ == "__main__"
    # but no __file__, so an unguarded block would run on every evaluation and fail.
    import sys
    fid = sys.argv[1] if len(sys.argv) > 1 else "full"
    print(json.dumps(evaluate(str(HERE), fidelity=fid), indent=1))

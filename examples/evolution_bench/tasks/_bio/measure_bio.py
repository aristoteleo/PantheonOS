"""Final measurement of the single-cell runs (molt_biology.md, Analysis step 1), one task per call.

Per run (results/<root>/<task>_<method>_s<seed>): its top-5 distinct programs by the train score
recorded during the run are each read once on the valid datasets; the best valid score is the
run's pick (ties: higher train score; a program failing on valid is out). Every pick and the start
program are then read READS times on the test datasets, interleaved (read 1 of every program, then
read 2, ...); a run's final score is that mean (a failed test read counts 0). A run with no
program passing valid scores as the start program. Nothing read on test feeds back into a choice.

    python measure_bio.py RESULTS_ROOT TASK [--reads=3] [--top=5] [--par=2] [--dry]
        -> RESULTS_ROOT/measure_<task>.json and RESULTS_ROOT/finals/<run>.py
Resumes from an existing output file. Run in a job with 8 CPUs per parallel read (same thread
count as the eval servers), with the eval-server environment (venv-bio).
"""
import hashlib
import json
import os
import shutil
import statistics as S
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = HERE.parent


def one(task: str, split: str, src: str, out: str):
    """A single read in this process: program file `src` on `split` -> metrics JSON at `out`."""
    os.environ["BIO_SPLIT"] = split
    os.environ["TASK_DIR"] = str(TASKS / task)
    sys.path.insert(0, str(TASKS / task))
    import importlib.util
    spec = importlib.util.spec_from_file_location("evaluator", TASKS / task / "evaluator.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    assert ev.SPLIT == split
    ws = tempfile.mkdtemp(prefix="measure_")
    try:
        shutil.copy(src, Path(ws) / "solution.py")
        m = ev.evaluate(ws, "full")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    Path(out).write_text(json.dumps(m, default=str))


def bare(code: str) -> str:
    return "\n".join(l for l in code.strip().splitlines() if "EVOLVE-BLOCK-" not in l).strip()


def sha(code: str) -> str:
    return hashlib.sha256(bare(code).encode()).hexdigest()[:16]


def history(d: Path):
    """[(recorded train score, id, code)] of a run's evaluated programs (full-fidelity reads only)."""
    out = []
    if (d / "store.json").exists():                       # run_bench.py (molt, simpletes)
        for it in json.load(open(d / "store.json"))["individuals"]:
            ms = sorted((m for m in it.get("measurements") or [] if isinstance(m, dict)
                         and m.get("fidelity", "full") == "full"), key=lambda m: m.get("at") or 0)
            code = next(iter(it["genome"].get("files", {}).values()), "")
            sc = (ms[0].get("metrics") or {}).get("combined_score") if ms else None
            if code and isinstance(sc, (int, float)):
                out.append((float(sc), it["id"], code))
    elif (d / "evals.jsonl").exists():                    # run_upstream_other.py (openevolve, shinka, aide)
        for line in open(d / "evals.jsonl"):
            if not line.strip():
                continue
            r = json.loads(line)
            p = d / "progs" / f"{r['sha']}.py"
            if p.exists() and isinstance(r.get("score"), (int, float)):
                out.append((float(r["score"]), r["sha"], p.read_text()))
    return out


def top(hist, start_code: str, k: int):
    seen, res = {bare(start_code)}, []
    for sc, pid, code in sorted((h for h in hist if h[0] > 0), key=lambda h: -h[0]):
        if bare(code) in seen:
            continue
        seen.add(bare(code))
        res.append({"id": pid, "train": sc, "sha": sha(code), "code": code})
        if len(res) == k:
            break
    return res


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    root, task = Path(args[0]), args[1]
    reads, k, par = int(opt.get("reads", 3)), int(opt.get("top", 5)), int(opt.get("par", 2))
    start = (TASKS / task / "solution.py").read_text()
    out = root / f"measure_{task}.json"
    res = json.loads(out.read_text()) if out.exists() else {"task": task, "valid": {}, "test": {}}
    work = Path(tempfile.mkdtemp(prefix="measure_bio_"))
    codes = {sha(start): start}

    runs = {}
    for d in sorted(root.glob(f"{task}_*_s*")):
        if d.is_dir() and d.name.rsplit("_s", 1)[1].isdigit():
            runs[d.name] = top(history(d), start, k)
            for c in runs[d.name]:
                codes[c["sha"]] = c["code"]
    print({r: [round(c["train"], 4) for c in t] for r, t in runs.items()}, flush=True)
    if "--dry" in sys.argv:
        return

    def read(split, h):
        src, dst = work / f"{h}.py", work / f"{h}.{split}.{time.time_ns()}.json"
        src.write_text(codes[h])
        t = time.time()
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one", task, split, str(src), str(dst)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        m = json.loads(dst.read_text()) if dst.exists() else {
            "combined_score": 0.0, "invalid_reason": "read crashed: " + p.stdout.decode(errors="replace")[-800:]}
        return {"score": float(m.get("combined_score") or 0.0), "wall": round(time.time() - t, 1),
                "datasets": {n: v.get("total", v.get("score")) for n, v in (m.get("datasets") or {}).items()},
                "error": m.get("invalid_reason")}

    def dump():
        out.write_text(json.dumps(res, indent=1))

    # valid: every candidate and the start program, once
    need = [h for h in dict.fromkeys([sha(start)] + [c["sha"] for t in runs.values() for c in t])
            if h not in res["valid"]]
    print(f"valid: {len(need)} programs to read", flush=True)
    with ThreadPoolExecutor(par) as ex:
        for h, m in zip(need, ex.map(lambda h: read("valid", h), need)):
            res["valid"][h] = m
            dump()

    # pick per run
    res["runs"] = {}
    for r, t in runs.items():
        ok = [c for c in t if res["valid"][c["sha"]]["score"] > 0 and not res["valid"][c["sha"]]["error"]]
        pick = max(ok, key=lambda c: (res["valid"][c["sha"]]["score"], c["train"])) if ok else None
        res["runs"][r] = {"candidates": [{"id": c["id"], "sha": c["sha"], "train": c["train"],
                                          "valid": res["valid"][c["sha"]]["score"]} for c in t],
                          "pick": pick["sha"] if pick else sha(start), "pick_id": pick["id"] if pick else "start"}
    dump()

    # test: picks and the start program, READS interleaved reads
    picks = list(dict.fromkeys([sha(start)] + [v["pick"] for v in res["runs"].values()]))
    for i in range(reads):
        need = [h for h in picks if len(res["test"].get(h, [])) <= i]
        with ThreadPoolExecutor(par) as ex:
            for h, m in zip(need, ex.map(lambda h: read("test", h), need)):
                res["test"].setdefault(h, []).append(m)
                dump()
        print(f"test read {i + 1}/{reads} done", flush=True)

    fin = root / "finals"
    fin.mkdir(exist_ok=True)
    res["start"] = {"valid": res["valid"][sha(start)]["score"],
                    "test": S.mean(m["score"] for m in res["test"][sha(start)])}
    for r, v in res["runs"].items():
        ms = res["test"][v["pick"]]
        v["test_reads"] = [m["score"] for m in ms]
        v["final"] = S.mean(v["test_reads"])
        names = dict.fromkeys(n for m in ms for n in m["datasets"])
        v["test_datasets"] = {n: S.mean(m["datasets"].get(n) or 0.0 for m in ms) for n in names}
        (fin / f"{r}.py").write_text(codes[v["pick"]])
    dump()
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"start": res["start"], **{r: round(v["final"], 4) for r, v in res["runs"].items()}}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        one(*sys.argv[2:6])
    else:
        main()

"""Pilot measurements of the single-cell tasks (molt_biology.md, step 1): per task, the start
program read 3 times on the train datasets (time, read-to-read spread) and once on valid and test,
and every known-method reference (refs/<task>__<method>.py) read once on each split.

    python pilot_bio.py OUT.json [--extra=DIR] [task ...]   # run with the reference env (venv-bio-ref)

--extra=DIR adds programs DIR/<integration|denoise>__<name>.py, read once on each split like a
reference (used for the best programs of the short pilot runs, which searched on the first split).
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = HERE.parent


def load_eval(task):
    os.environ["TASK_DIR"] = str(TASKS / task)
    spec = importlib.util.spec_from_file_location(f"ev_{task}", TASKS / task / "evaluator.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read(task, src: Path, split: str):
    os.environ["BIO_SPLIT"] = split
    import biocommon
    biocommon.SPLIT = split
    ev = load_eval(task)
    ev.SPLIT = split
    ws = tempfile.mkdtemp(prefix="pilot_")
    try:
        shutil.copy(src, Path(ws) / "solution.py")
        t = time.time()
        m = ev.evaluate(ws, "full")
        m["wall"] = round(time.time() - t, 1)
        return m
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main():
    out = Path(sys.argv[1])
    extra = [Path(a.split("=", 1)[1]) for a in sys.argv[2:] if a.startswith("--extra=")]
    tasks = [a for a in sys.argv[2:] if not a.startswith("--")] or ["bio_integration", "bio_denoise"]
    sys.path.insert(0, str(HERE))
    res = json.loads(out.read_text()) if out.exists() else {}
    for task in tasks:
        kind = task.removeprefix("bio_")
        r = res.setdefault(task, {})
        progs = {"start": TASKS / task / "solution.py"}
        for d in [HERE / "refs"] + extra:
            progs.update({p.stem.split("__")[1]: p for p in sorted(d.glob(f"{kind}__*.py"))})
        for name, src in progs.items():
            reads = 3 if name == "start" else 1
            for split in ("train", "valid", "test"):
                key = f"{name}/{split}"
                have = r.get(key, [])
                for _ in range(len(have), reads if split == "train" else 1):
                    m = read(task, src, split)
                    have.append(m)
                    r[key] = have
                    out.write_text(json.dumps(res, indent=1, default=str))
                    per = " ".join(f"{k}={v.get('total', v.get('score')):.3f}" for k, v in (m.get("datasets") or {}).items())
                    print(f"{task:16s} {key:20s} score={m.get('combined_score'):.4f} wall={m['wall']}s  {per} "
                          f"{m.get('invalid_reason', '')[:200]}", flush=True)


if __name__ == "__main__":
    main()

"""Shared harness of the single-cell tasks (bio_integration, bio_denoise).

A program (`solution.py`) runs in its own process -- fresh interpreter, capped threads, a wall-clock
limit per dataset, its own temporary directory -- and hands back an array file; the evaluator
scores that array in this process. A program never sees the data directory, only copies of its
input files.

Environment:
  BIO_DATA           prepared inputs (prep_bio.py output); default $SCRATCH/evolve/biodata/prep
  BIO_SPLIT          "train" (what a search sees), "valid" (picks a run's final program) or
                     "test" (final scores only); see SPLITS
  BIO_SOLVE_SECONDS  wall-clock limit per dataset (default 300)
  BIO_THREADS        BLAS/OpenMP/numba threads a program may use (default 8)
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(os.environ.get("BIO_DATA") or Path(os.environ.get("SCRATCH", "/tmp")) / "evolve/biodata/prep")
SPLIT = os.environ.get("BIO_SPLIT", "train")
SOLVE_SECONDS = int(os.environ.get("BIO_SOLVE_SECONDS", "300"))
THREADS = os.environ.get("BIO_THREADS", "8")

# Three disjoint sets of studies (molt_biology.md). train: the fitness every method searches on.
# valid: read once per run, to pick its final program among its best on train. test: read only for
# that pick -- tissues, species and protocols absent from train and valid.
_TRAIN = ["pancreas", "lung", "hypomap"]            # human pancreas (CEL-seq / inDrop / SMART), human lung (10x / Drop-seq), mouse hypothalamus
_VALID = ["immune_human", "tms_muscle"]             # human bone marrow + blood, mouse skeletal muscle
_TEST = ["kidney_dkd", "heart", "zebrafish_5dpf", "gtex"]   # human kidney nuclei, human heart cells + nuclei, zebrafish larva, 8 human tissues (nuclei)
SPLITS = {t: {"train": _TRAIN, "valid": _VALID, "test": _TEST} for t in ("integration", "denoise")}

_RUNNER = r'''
import importlib.util, json, sys, time
import numpy as np, anndata as ad
sol, fn, out = sys.argv[1], sys.argv[2], sys.argv[3]
inputs = [ad.read_h5ad(p) for p in sys.argv[4:]]
spec = importlib.util.spec_from_file_location("solution", sol)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
t = time.time()
res = getattr(mod, fn)(*inputs)
secs = time.time() - t
np.save(out, np.asarray(res, dtype=np.float32))
print("SOLVE_SECONDS", round(secs, 2))
'''


def run_program(workspace: str, fn: str, inputs: list[Path], out_name: str):
    """-> (output path or None, error text, seconds). The program's function `fn` gets the inputs
    as AnnData objects, in order."""
    sol = Path(workspace) / "solution.py"
    if not sol.exists():
        return None, "solution.py missing", 0.0
    tmp = Path(tempfile.mkdtemp(prefix="bio_", dir=os.environ.get("EVAL_TMP") or None))
    try:
        local = []
        for p in inputs:
            shutil.copy(p, tmp / p.name)
            local.append(str(tmp / p.name))
        (tmp / "_runner.py").write_text(_RUNNER)
        shutil.copy(sol, tmp / "solution.py")
        out = tmp / out_name
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BIO_", "OPENROUTER", "OPENAI"))}
        env.update({k: THREADS for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                          "NUMBA_NUM_THREADS", "XLA_CPU_THREADS")})
        env.update({"HOME": str(tmp), "NUMBA_CACHE_DIR": str(tmp / ".numba"), "MPLCONFIGDIR": str(tmp / ".mpl"),
                    "PYTHONHASHSEED": "0"})
        t0 = time.time()
        p = subprocess.Popen([sys.executable, "_runner.py", "solution.py", fn, str(out), *local], cwd=tmp,
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            log, _ = p.communicate(timeout=SOLVE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return None, f"time limit: {SOLVE_SECONDS} s", time.time() - t0
        secs = time.time() - t0
        text = log.decode(errors="replace")
        if p.returncode != 0 or not out.exists():
            return None, f"program failed (exit {p.returncode}): {text[-1500:]}", secs
        keep = Path(workspace) / f".out_{out_name}"
        shutil.move(str(out), keep)
        return keep, "", secs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fail(reason: str, t0: float, extra: dict | None = None) -> dict:
    return {"combined_score": 0.0, "validity": 0.0, "invalid_reason": reason[:2000],
            "eval_time": time.time() - t0, **(extra or {})}


def fidelity_of(workspace: str, fidelity: str) -> str:
    marker = Path(workspace) / ".fidelity"
    if marker.exists():
        return marker.read_text().strip() or fidelity
    return fidelity

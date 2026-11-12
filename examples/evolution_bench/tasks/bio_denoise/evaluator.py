"""Single-cell denoising by molecular cross-validation (Batson et al. 2019; the OpenProblems
denoising task's design).

Every count of a real dataset was split binomially (tasks/_bio/prep_bio.py): 90 % form the input,
10 % are held back. `solution.py` defines `denoise(adata) -> matrix` (cells x genes, >= 0): the
expected expression of every cell and gene. It is scored on how well it predicts the held-back
counts, after rescaling it to their total:

- mse: mean squared error of log1p(prediction) vs log1p(held-back counts);
- poisson: mean Poisson negative log-likelihood of the held-back counts.

Each is normalised between "no denoising" (the input itself) = 0 and "perfect" (the held-back
counts themselves) = 1; a dataset's score is their mean, floored at 0 (worse than not denoising
counts as not denoising). combined_score = mean over the split's datasets.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(os.environ.get("TASK_DIR") or globals().get("__file__", ".")).resolve()
sys.path.insert(0, str(HERE.parent / "_bio"))
from biocommon import DATA, SPLIT, SPLITS, fail, fidelity_of, run_program  # noqa: E402

DATASETS = SPLITS["denoise"]   # train / valid / test: disjoint studies, see _bio/biocommon.py


def _losses(pred: np.ndarray, test: np.ndarray) -> tuple[float, float]:
    p = pred * (test.sum() / max(float(pred.sum()), 1e-12))
    mse = float(np.mean((np.log1p(p) - np.log1p(test)) ** 2))
    poisson = float(np.mean(p - test * np.log(p + 1e-6)))
    return mse, poisson


def score(pred: np.ndarray, train: np.ndarray, test: np.ndarray) -> dict:
    m, q = _losses(pred, test)
    m0, q0 = _losses(train, test)
    m1, q1 = _losses(test, test)
    mse_n, poi_n = (m0 - m) / (m0 - m1), (q0 - q) / (q0 - q1)
    return {"mse": m, "poisson": q, "mse_norm": mse_n, "poisson_norm": poi_n,
            "score": max(0.0, (mse_n + poi_n) / 2)}


def evaluate(workspace_path: str, fidelity: str = "full") -> dict:
    import anndata as ad
    import scipy.sparse as sp

    t0 = time.time()
    names = DATASETS[SPLIT]
    if fidelity_of(workspace_path, fidelity) == "low":
        names = names[:1]
    out = {}
    for name in names:
        inp = DATA / "denoise" / f"{name}.input.h5ad"
        path, err, secs = run_program(workspace_path, "denoise", [inp], f"{name}.npy")
        if path is None:
            return fail(f"{name}: {err}", t0)
        pred = np.load(path).astype(np.float64)
        train = ad.read_h5ad(inp).X
        train = (train.toarray() if sp.issparse(train) else np.asarray(train)).astype(np.float64)
        test = sp.load_npz(DATA / "denoise" / f"{name}.test.npz").toarray().astype(np.float64)
        if pred.shape != test.shape:
            return fail(f"{name}: output shape {pred.shape}, need {test.shape}", t0)
        if not np.all(np.isfinite(pred)) or pred.min() < 0 or pred.sum() <= 0:
            return fail(f"{name}: output must be finite, non-negative and not all zero", t0)
        out[name] = {**score(pred, train, test), "solve_seconds": round(secs, 1)}
    combined = float(np.mean([v["score"] for v in out.values()]))
    return {"combined_score": combined, "validity": 1.0, "datasets": out,
            **{f"{k}_score": v["score"] for k, v in out.items()},
            "eval_time": time.time() - t0, "fitness_weights": {"combined_score": 1.0}}


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate(sys.argv[1] if len(sys.argv) > 1 else str(HERE),
                              sys.argv[2] if len(sys.argv) > 2 else "full"), indent=1, default=str))
